"""Spend anomaly detection — a day that broke from its own baseline.

The statistics here are deliberately unambitious: a trailing median and a
ratio. No seasonality model, no z-score, no changepoint detection. The reason
is that this feature's job is to produce an alert a human will act on, and the
binding constraint on that is false positives, not detection power. An
anomaly detector that cries twice a week is switched off inside a month, at
which point its detection power is zero.

So the four guards below all trade sensitivity for trustworthiness, and each
is a specific way a naive `today > 2 * average` would embarrass itself:

1. **Median, not mean.** If the tenant spiked last Tuesday, that spike is in
   the window. A mean drags upward far enough that the *next* spike — often
   the same runaway process, still running — falls under the threshold and
   goes unreported. A median shrugs the earlier spike off. This is the single
   most consequential line in the file.

2. **An absolute floor, not just a ratio.** $0.02 to $0.30 is a 15x increase
   and means nothing whatsoever. Ratios on small numbers are noise
   amplifiers, and without a floor the detector's output is dominated by the
   tenants who spend least.

3. **Enough history, or nothing.** With three days of ledger there is no
   baseline, only three numbers. Reporting the largest as an anomaly is
   describing the sample, not the tenant. Same discipline as slice 4 refusing
   to project month-end before a week has elapsed.

4. **Finalized days only.** Today's rows are `is_provisional` and usually
   partial — a vendor reports the day as it goes. Evaluating them would flag
   the morning's incomplete figure and un-flag it by evening, producing
   exactly the flapping alert that teaches people to ignore the channel.

A detector nobody trusts is worse than no detector, because it occupies the
slot a trustworthy one would have filled.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_cost_record import AICostRecord, CostProvider
from app.models.cost_anomaly import CostAnomaly
from app.models.user import Role, User, UserRole
from app.services.email_service import send_email

logger = structlog.get_logger(__name__)

# Days of finalized history the baseline is drawn from. Three weeks covers
# three of each weekday, so a Monday is compared against a window that
# contains Mondays rather than against a flat average of everything.
BASELINE_WINDOW_DAYS = 21

# Below this many days in the window there is no baseline worth the name.
MIN_BASELINE_DAYS = 7

# How many times the baseline a day must reach to count. 3x is high enough
# that ordinary weekday/weekend variation never trips it, and low enough to
# catch a doubling-and-then-some.
DEFAULT_RATIO_THRESHOLD = Decimal("3.0")

# ...and it must also be this many dollars above the baseline. This is guard 2:
# without it, every tenant whose daily spend rounds to cents generates alerts.
MIN_ABSOLUTE_DELTA_USD = Decimal("25.00")

_CENTS = Decimal("0.01")


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class AnomalyFinding:
    """A day that cleared every guard."""

    provider: CostProvider | None
    usage_date: date
    observed_usd: Decimal
    baseline_usd: Decimal
    ratio: Decimal
    baseline_days: int


def _median(values: list[Decimal]) -> Decimal:
    """Median of a non-empty list, as a 2dp Decimal.

    `statistics.median` on Decimals averages the middle two for even-length
    input, which stays exact for Decimal inputs — no float conversion.
    """
    return _money(statistics.median(values))


def evaluate_series(
    daily: dict[date, Decimal],
    *,
    provider: CostProvider | None,
    as_of: date,
    ratio_threshold: Decimal = DEFAULT_RATIO_THRESHOLD,
) -> AnomalyFinding | None:
    """Judge `as_of` against the days before it. Pure, so it is testable.

    `daily` must contain only finalized days (guard 4 is the caller's job —
    see `_finalized_daily`). Returns None whenever any guard is unmet, and the
    reason is deliberately not surfaced: a caller that branched on "why not"
    would be re-implementing the policy this function exists to own.
    """
    observed = daily.get(as_of)
    if observed is None:
        return None

    window_start = as_of - timedelta(days=BASELINE_WINDOW_DAYS)
    history = [v for d, v in daily.items() if window_start <= d < as_of]

    # Guard 3: enough history, or nothing.
    if len(history) < MIN_BASELINE_DAYS:
        return None

    baseline = _median(history)

    # A zero baseline makes the ratio undefined. Same shape as the ROI
    # no-denominator rule: the honest answer is "no comparison available",
    # not an infinity. First-ever spend is not an anomaly, it is a start.
    if baseline <= 0:
        return None

    # Guard 2: the jump must be materially large in dollars, not just in
    # proportion.
    if observed - baseline < MIN_ABSOLUTE_DELTA_USD:
        return None

    ratio = (observed / baseline).quantize(_CENTS, rounding=ROUND_HALF_UP)
    if ratio < ratio_threshold:
        return None

    return AnomalyFinding(
        provider=provider,
        usage_date=as_of,
        observed_usd=observed,
        baseline_usd=baseline,
        ratio=ratio,
        baseline_days=len(history),
    )


async def _finalized_daily(
    db: AsyncSession,
    *,
    tenant_id: object,
    provider: CostProvider | None,
    since: date,
    until: date,
) -> dict[date, Decimal]:
    """Daily spend totals, finalized rows only (guard 4).

    Provisional rows are excluded rather than merely flagged: a partially
    reported day is not a smaller day, it is an unknown one, and averaging
    unknowns into a baseline drags it down and manufactures anomalies.

    Tenant filtered explicitly alongside RLS, matching the rest of the cost
    services — we never rely on RLS alone.
    """
    query = (
        select(AICostRecord.usage_date, func.sum(AICostRecord.cost_usd))
        .where(
            AICostRecord.tenant_id == tenant_id,
            AICostRecord.usage_date >= since,
            AICostRecord.usage_date <= until,
            AICostRecord.is_provisional.is_(False),
        )
        .group_by(AICostRecord.usage_date)
    )
    if provider is not None:
        query = query.where(AICostRecord.provider == provider)

    return {row[0]: _money(row[1]) for row in (await db.execute(query)).all()}


async def detect_for_tenant(
    db: AsyncSession,
    tenant_id: object,
    *,
    as_of: date | None = None,
) -> list[AnomalyFinding]:
    """Run detection for a tenant across the tenant-wide and per-provider series.

    `as_of` defaults to *yesterday*, not today: today is still provisional and
    guard 4 would exclude it anyway, so defaulting to today would silently
    evaluate nothing and look like "no anomalies".
    """
    as_of = as_of or (datetime.now(UTC).date() - timedelta(days=1))
    since = as_of - timedelta(days=BASELINE_WINDOW_DAYS)

    # Which providers this tenant actually has finalized spend for. Iterating
    # the whole CostProvider enum instead would run a query per provider the
    # tenant has never used, every day, forever.
    active = (
        (
            await db.execute(
                select(AICostRecord.provider)
                .where(
                    AICostRecord.tenant_id == tenant_id,
                    AICostRecord.usage_date >= since,
                    AICostRecord.usage_date <= as_of,
                    AICostRecord.is_provisional.is_(False),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    findings: list[AnomalyFinding] = []
    for provider in [None, *active]:
        daily = await _finalized_daily(
            db, tenant_id=tenant_id, provider=provider, since=since, until=as_of
        )
        finding = evaluate_series(daily, provider=provider, as_of=as_of)
        if finding is not None:
            findings.append(finding)

    return findings


async def persist_findings(
    db: AsyncSession,
    tenant_id: object,
    findings: list[AnomalyFinding],
) -> list[CostAnomaly]:
    """Upsert findings, returning the rows that are new or materially changed.

    An existing row for the same (tenant, provider, day) is refreshed rather
    than duplicated. Crucially, an **acknowledged** row is left entirely alone:
    someone has already explained this spike, and re-detecting it must not
    resurrect it as unacknowledged and alert again.
    """
    touched: list[CostAnomaly] = []
    now = datetime.now(UTC)

    for f in findings:
        existing = (
            await db.execute(
                select(CostAnomaly).where(
                    CostAnomaly.tenant_id == tenant_id,
                    CostAnomaly.usage_date == f.usage_date,
                    CostAnomaly.provider.is_(None)
                    if f.provider is None
                    else CostAnomaly.provider == f.provider,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            if existing.acknowledged_at is not None:
                continue
            existing.observed_usd = f.observed_usd
            existing.baseline_usd = f.baseline_usd
            existing.ratio = f.ratio
            existing.baseline_days = f.baseline_days
            touched.append(existing)
            continue

        row = CostAnomaly(
            tenant_id=tenant_id,
            provider=f.provider,
            usage_date=f.usage_date,
            observed_usd=f.observed_usd,
            baseline_usd=f.baseline_usd,
            ratio=f.ratio,
            baseline_days=f.baseline_days,
            detected_at=now,
        )
        db.add(row)
        touched.append(row)

    return touched


def _scope_label(provider: CostProvider | None) -> str:
    return "AI spend" if provider is None else f"{provider.value} spend"


def render_anomaly_alert(row: CostAnomaly) -> tuple[str, str]:
    """Subject and HTML body for one detected spike.

    The baseline and its length are both in the body. "Spend was 5x normal"
    invites the reader to ask what normal means, and an alert that cannot
    answer that question gets treated as noise.
    """
    scope = _scope_label(row.provider)
    subject = f"Unusual {scope}: {row.ratio}x the usual daily amount"
    body = (
        f"<p>On {row.usage_date:%-d %B %Y}, {scope} was "
        f"<strong>${row.observed_usd}</strong> — about <strong>{row.ratio}x</strong> "
        f"the median of <strong>${row.baseline_usd}</strong>/day over the preceding "
        f"{row.baseline_days} days.</p>"
        "<p>Worth a look if you were not expecting it: a retry loop, a backfill, or a "
        "key used outside its intended scope all show up this way.</p>"
        "<p>Only finalized days are checked, so this is not a partially-reported "
        "figure. You can acknowledge it in Prompt Shields to stop it being raised "
        "again.</p>"
    )
    return subject, body


async def _alert_recipients(db: AsyncSession, tenant_id: object) -> list[str]:
    """Active admin emails, matching the budget alerts' audience."""
    result = await db.execute(
        select(User.email)
        .join(UserRole, UserRole.user_id == User.id)
        .where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            UserRole.role.in_((Role.ORG_ADMIN, Role.TENANT_ADMIN)),
        )
        .distinct()
    )
    return [e for e in result.scalars().all() if e]


async def detect_and_alert(
    db: AsyncSession,
    tenant_id: object,
    *,
    as_of: date | None = None,
) -> int:
    """Full pass for one tenant: detect, persist, mail the new ones.

    Caller sets the tenant GUC. Returns the number of anomalies alerted.

    Only rows with no `alerted_at` are mailed, so a spike that is re-detected
    on the next run (the window overlaps deliberately) stays quiet. The stamp
    goes on after a successful send for the same reason as slice 4's: stamping
    first would let a bounced mail silence the anomaly permanently.
    """
    findings = await detect_for_tenant(db, tenant_id, as_of=as_of)
    rows = await persist_findings(db, tenant_id, findings)
    await db.flush()

    to_alert = [r for r in rows if r.alerted_at is None]
    if not to_alert:
        await db.commit()
        return 0

    recipients = await _alert_recipients(db, tenant_id)
    if not recipients:
        logger.warning("anomaly_alert_no_recipients", tenant_id=str(tenant_id))
        await db.commit()
        return 0

    sent = 0
    now = datetime.now(UTC)
    for row in to_alert:
        subject, body = render_anomaly_alert(row)
        delivered = False
        for address in recipients:
            try:
                await send_email(address, subject, body)
                delivered = True
            except Exception as exc:  # noqa: BLE001 — one bad address must not stop the rest.
                logger.warning(
                    "anomaly_alert_send_failed",
                    tenant_id=str(tenant_id),
                    usage_date=str(row.usage_date),
                    error=str(exc),
                )
        if delivered:
            row.alerted_at = now
            sent += 1

    await db.commit()
    return sent
