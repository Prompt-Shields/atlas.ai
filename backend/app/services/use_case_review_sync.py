"""Use-case re-attestation enqueue service.

Runs periodically (daily via the `reattestation_enqueue` worker
mode). For each Active use case in a tenant:

  - If no Review exists yet, OR the most recent Review is older
    than (last_review.due_at + REVIEW_CADENCE_DAYS) — create a new
    Review with status=DUE and due_at=now+REVIEW_GRACE_DAYS
  - Bump existing DUE rows to OVERDUE if due_at + grace has passed
    without a review

This is the back-pressure mechanism for keeping the registry
fresh. The /dashboard/reattestation page surfaces what's due.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.use_case import UseCase, UseCaseStatus
from app.models.use_case_review import ReviewStatus, UseCaseReview

logger = structlog.get_logger()


# Cadence: re-attest every 90 days. Tunable per tenant in v0.2.
REVIEW_CADENCE_DAYS = 90
# Grace period: due_at is N days after enqueue, after which the
# row flips from DUE → OVERDUE.
REVIEW_GRACE_DAYS = 14


async def enqueue_due_reviews(db: AsyncSession) -> int:
    """Create new Review rows for use cases whose last review (or
    registration) is older than the cadence.

    Bumps existing DUE rows to OVERDUE if past due_at.

    Returns the number of rows created or transitioned.
    """
    now = datetime.now(UTC)
    rolled_over = 0
    enqueued = 0

    # ── Roll over expired DUE → OVERDUE ──────────────────────────
    expired_q = select(UseCaseReview).where(
        UseCaseReview.status == ReviewStatus.DUE,
        UseCaseReview.due_at < now,
    )
    for row in (await db.execute(expired_q)).scalars().all():
        row.status = ReviewStatus.OVERDUE
        rolled_over += 1

    # ── Enqueue new Reviews for stale use cases ──────────────────
    # We only attest Active use cases (Draft/Review/Retired skipped).
    cadence_cutoff = now - timedelta(days=REVIEW_CADENCE_DAYS)
    use_case_q = select(UseCase).where(UseCase.status == UseCaseStatus.ACTIVE)
    for uc in (await db.execute(use_case_q)).scalars().all():
        # Find the most-recent Review for this use case.
        last_q = (
            select(UseCaseReview)
            .where(UseCaseReview.use_case_id == uc.id)
            .order_by(desc(UseCaseReview.created_at))
            .limit(1)
        )
        last = (await db.execute(last_q)).scalar_one_or_none()

        should_enqueue = False
        if last is None:
            # Never reviewed — gate on registration age.
            reg_aware = (
                uc.created_at
                if uc.created_at.tzinfo is not None
                else uc.created_at.replace(tzinfo=UTC)
            )
            if reg_aware <= cadence_cutoff:
                should_enqueue = True
        elif last.status in (
            ReviewStatus.REVIEWED,
            ReviewStatus.DISMISSED,
        ):
            # Already completed — check whether next cycle is due.
            anchor = last.reviewed_at or last.created_at
            anchor_aware = anchor if anchor.tzinfo is not None else anchor.replace(tzinfo=UTC)
            if anchor_aware <= cadence_cutoff:
                should_enqueue = True
        # Existing DUE / OVERDUE rows: don't enqueue a duplicate.

        if not should_enqueue:
            continue

        db.add(
            UseCaseReview(
                tenant_id=uc.tenant_id,
                use_case_id=uc.id,
                due_at=now + timedelta(days=REVIEW_GRACE_DAYS),
                status=ReviewStatus.DUE,
            )
        )
        enqueued += 1

    if rolled_over or enqueued:
        await db.commit()
        logger.info(
            "use_case_review_enqueue",
            rolled_over=rolled_over,
            enqueued=enqueued,
        )

    return rolled_over + enqueued
