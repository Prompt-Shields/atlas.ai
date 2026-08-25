"""Drift signal dispatcher — translates rule-engine findings into
UseCaseReview rows.

Sits next to `use_case_review_sync.py`. Both create Review rows;
this one fires off the 90-day cadence (event-triggered, not time-
triggered). The reattestation_reminder_dispatcher then DMs the
owner whichever path the review came in on.

Flow per tick:

  1. Iterate Active use cases.
  2. Apply `detect_drift()` (pure) → list of findings.
  3. For each use case with findings:
     a. If an open Review (DUE/OVERDUE) already exists →
        - If its notes already mention every finding's signature → skip.
        - Otherwise: append the new findings + flip drift_observed=True,
          carrying forward the existing due_at.
     b. If no open Review exists → create one with status=DUE,
        drift_observed=True, due_at=now+DRIFT_GRACE_DAYS (shorter
        than the 14-day normal grace because drift is actionable now),
        notes=encoded findings.

The dispatcher never deletes or downgrades existing rows. Notes are
append-only; the worst case is a long notes blob with stale findings,
which the IT lead can clear by marking reviewed.

Idempotency: the signature-in-notes dedup makes this safe to run
every tick. Running once an hour produces the same outcome as
running once a day, modulo the freshness of the rule set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift_signal import TenantDriftSignal
from app.models.use_case import UseCase, UseCaseStatus
from app.models.use_case_review import ReviewStatus, UseCaseReview
from app.services.drift_signals import (
    DriftFinding,
    TenantSignalSpec,
    detect_drift,
    encode_findings_for_notes,
    signature_in_notes,
)

logger = structlog.get_logger()


# Drift-triggered reviews use a shorter grace than the standard
# enqueue path (14d) because the drift is actionable *now*, not
# someday. 7 days is enough for the owner to receive the DM, pick
# it up, and respond without becoming OVERDUE just from latency.
DRIFT_GRACE_DAYS = 7


async def dispatch_drift_signals(db: AsyncSession) -> dict[str, int]:
    """Scan use cases, apply rules, create/augment Reviews.

    Returns a small stats dict for logging:
      use_cases_scanned, findings_emitted, reviews_created,
      reviews_augmented, reviews_unchanged
    """
    now = datetime.now(UTC)
    stats = {
        "use_cases_scanned": 0,
        "findings_emitted": 0,
        "reviews_created": 0,
        "reviews_augmented": 0,
        "reviews_unchanged": 0,
    }

    use_cases = (
        (await db.execute(select(UseCase).where(UseCase.status == UseCaseStatus.ACTIVE)))
        .scalars()
        .all()
    )

    # Load tenant signals once, group by tenant_id so each use case
    # only sees its own tenant's overrides. Cheap query — typically
    # single-digit signals per tenant, single-digit tenants per
    # deployment.
    tenant_signals_by_tenant = await _load_active_tenant_signals(db)

    for uc in use_cases:
        stats["use_cases_scanned"] += 1
        tenant_signals = tenant_signals_by_tenant.get(uc.tenant_id, [])
        findings = detect_drift(uc, tenant_signals=tenant_signals)
        if not findings:
            continue
        stats["findings_emitted"] += len(findings)

        existing = await _open_review_for(db, uc.id)

        if existing is None:
            await _create_drift_review(db, uc, findings, now=now)
            stats["reviews_created"] += 1
        else:
            mutated = _augment_existing(existing, findings)
            if mutated:
                stats["reviews_augmented"] += 1
            else:
                stats["reviews_unchanged"] += 1

    if stats["reviews_created"] or stats["reviews_augmented"]:
        await db.commit()

    if stats["findings_emitted"] > 0:
        logger.info("drift_signal_batch", **stats)

    return stats


# ─── DB helpers ──────────────────────────────────────────────────────


async def _load_active_tenant_signals(
    db: AsyncSession,
) -> dict:
    """Return active TenantDriftSignal rows grouped by tenant_id."""
    rows = (
        (await db.execute(select(TenantDriftSignal).where(TenantDriftSignal.is_active.is_(True))))
        .scalars()
        .all()
    )
    out: dict = {}
    for row in rows:
        spec = TenantSignalSpec(
            kind=row.kind.value if hasattr(row.kind, "value") else str(row.kind),
            match=row.match_pattern,
            label=row.label,
            severity=row.severity.value if hasattr(row.severity, "value") else str(row.severity),
            successor_or_current_name=row.successor_or_current_name,
            effective_as_of=row.effective_as_of,
        )
        out.setdefault(row.tenant_id, []).append(spec)
    return out


async def _open_review_for(db: AsyncSession, use_case_id) -> UseCaseReview | None:
    """Return the most-recent open (DUE or OVERDUE) Review, or None.

    "Open" here is the state where this drift is still relevant to
    surface — REVIEWED or DISMISSED rows are closed and a new
    Review is needed.
    """
    q = (
        select(UseCaseReview)
        .where(
            UseCaseReview.use_case_id == use_case_id,
            UseCaseReview.status.in_([ReviewStatus.DUE, ReviewStatus.OVERDUE]),
        )
        .order_by(desc(UseCaseReview.created_at))
        .limit(1)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _create_drift_review(
    db: AsyncSession,
    use_case: UseCase,
    findings: list[DriftFinding],
    *,
    now: datetime,
) -> None:
    review = UseCaseReview(
        tenant_id=use_case.tenant_id,
        use_case_id=use_case.id,
        status=ReviewStatus.DUE,
        due_at=now + timedelta(days=DRIFT_GRACE_DAYS),
        drift_observed=True,
        notes=encode_findings_for_notes(findings),
    )
    db.add(review)


def _augment_existing(review: UseCaseReview, findings: list[DriftFinding]) -> bool:
    """Append any *new* (not-yet-mentioned) findings to the existing
    review's notes. Returns True if we mutated the row, False if
    every finding was already present.

    The existing due_at is preserved — drift findings don't reset
    the clock if the IT lead is already on track to review.
    """
    new_findings = [f for f in findings if not signature_in_notes(review.notes, f.signature)]
    if not new_findings:
        return False

    appendix = encode_findings_for_notes(new_findings)
    if review.notes:
        review.notes = f"{review.notes}\n\n{appendix}"
    else:
        review.notes = appendix
    review.drift_observed = True
    return True
