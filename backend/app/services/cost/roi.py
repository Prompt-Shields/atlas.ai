"""AI ROI — the human-vs-AI value model. Cost ledger, slice 3.

The design doc calls this "the headline value", and that is precisely why it
needs the most care of the three slices. Slices 1 and 2 report money that was
actually spent. This one reports a *ratio*, and the numerator is an estimate no
one can observe directly. An ROI feature is the easiest place in a product to
tell a customer what they want to hear, so the rules below are structural
rather than a matter of taste:

**One measured half, one estimated half, never blended into one number without
saying which is which.** `ai_spend_usd` comes from the ledger — real invoices
and derived token cost, already provenance-tagged. `human_value_usd` is
`hours_saved x blended_hourly_rate`, and both of those factors are assumptions.
Every result therefore carries a `basis` and, when the inputs are not the
tenant's own, `is_illustrative`.

**No ROI without a denominator.** When a tenant has spent nothing in the
window, `roi_multiplier` is `None`, not infinity and not some large stand-in.
"Infinite ROI" is the single most misleading thing this module could emit, and
it is one unguarded division away.

**A loss is reported as a loss.** `net_value_usd` is signed. If AI costs more
than the time it saves, that is the finding, and floors or absolute values
would be the tool lying to the person paying for it.

**Assumptions come from one place** — `RoiAssumptions`, per tenant. Three
divergent hard-coded rates are what this slice replaced; see that model.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_cost_record import AICostRecord
from app.models.roi_assumptions import (
    DEFAULT_BLENDED_HOURLY_RATE_USD,
    HoursSavedSource,
    RoiAssumptions,
)
from app.services import adoption_service

logger = structlog.get_logger()

_CENTS = Decimal("0.01")


class HoursSavedBasis(str, enum.Enum):
    """What the hours-saved figure actually rests on, as opposed to intent.

    `HoursSavedSource` on the model records what the tenant asked for. This
    records what was available when the number was computed, which is not
    always the same thing — asking for the adoption pipeline gets you
    `sampled` until that pipeline reads the tenant's own telemetry.
    """

    # The tenant's own observed usage. Nothing produces this yet; it is
    # declared so the honest cases are not the special cases, and so the day
    # telemetry lands the presentation layer already handles it.
    measured = "measured"

    # The adoption pipeline's seeded sample. Representative of a plausible
    # organisation, not of *this* one.
    sampled = "sampled"

    # A figure an admin supplied. Theirs, and as good as their estimate.
    manual = "manual"


@dataclass(frozen=True)
class HoursSaved:
    """An hours-saved figure that always travels with its provenance."""

    hours_per_month: Decimal
    basis: HoursSavedBasis
    detail: str


@dataclass(frozen=True)
class RoiResult:
    window_start: date
    window_end: date
    window_days: int

    # Measured: the ledger's own total for the window.
    ai_spend_usd: Decimal

    # Estimated, and labelled as such all the way to the UI.
    hours_saved_per_month: Decimal
    hours_saved_in_window: Decimal
    blended_hourly_rate_usd: Decimal
    human_value_usd: Decimal

    # Derived from one of each.
    net_value_usd: Decimal
    roi_multiplier: Decimal | None

    basis: HoursSavedBasis
    basis_detail: str
    is_illustrative: bool


def _money(value: Decimal) -> Decimal:
    """Round to cents, half-up, the way an invoice would."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def seeded_hours_saved_per_month() -> Decimal:
    """The adoption pipeline's hours-saved figure, normalised to a month.

    `adoption_service` reports per *week* over a fixed 4-week window. Converting
    here rather than there keeps the scorecard's own arithmetic untouched, and
    the average-weeks-per-month factor is stated once instead of being folded
    into a magic number.
    """
    per_week = Decimal(str(adoption_service.build_scorecard().headline.hours_saved_per_week))
    weeks_per_month = Decimal("52") / Decimal("12")
    return _money(per_week * weeks_per_month)


def resolve_hours_saved(assumptions: RoiAssumptions) -> HoursSaved:
    """Turn the tenant's stated source into a figure plus its real provenance.

    A `manual` source with no number is *not* silently treated as zero — zero
    hours would render as a real, terrible ROI rather than as "you have not
    told us yet". It falls back to the pipeline and says so.
    """
    if assumptions.hours_saved_source is HoursSavedSource.manual:
        manual = assumptions.manual_hours_saved_per_month
        if manual is not None:
            return HoursSaved(
                hours_per_month=Decimal(str(manual)),
                basis=HoursSavedBasis.manual,
                detail="Supplied by an administrator for this organisation.",
            )
        logger.info(
            "roi_manual_hours_missing_falling_back",
            tenant_id=str(assumptions.tenant_id),
        )

    return HoursSaved(
        hours_per_month=seeded_hours_saved_per_month(),
        basis=HoursSavedBasis.sampled,
        detail=(
            "Derived from a representative sample, not from this organisation's "
            "own usage. Treat as an illustration until AI usage telemetry is "
            "connected, or enter your own estimate."
        ),
    )


def compute_roi(
    *,
    window_start: date,
    window_end: date,
    ai_spend_usd: Decimal,
    hours_saved: HoursSaved,
    blended_hourly_rate_usd: Decimal,
) -> RoiResult:
    """The whole model, as a pure function over already-fetched inputs.

    Pure so that every rule in this module's docstring is testable without a
    database — the guards below are the feature, and a guard that is awkward to
    test is a guard that rots.
    """
    # Inclusive of both endpoints: a single-day window is one day of spend, not
    # zero. An off-by-one here would silently scale every ROI figure.
    window_days = (window_end - window_start).days + 1
    if window_days < 1:
        raise ValueError("window_end must not precede window_start")

    days_per_month = Decimal("365.25") / Decimal("12")
    hours_in_window = _money(hours_saved.hours_per_month * (Decimal(window_days) / days_per_month))

    human_value = _money(hours_in_window * blended_hourly_rate_usd)
    spend = _money(ai_spend_usd)

    # Signed. A negative net value is a real answer.
    net_value = _money(human_value - spend)

    # No denominator, no ratio. Reporting "infinite ROI" for a tenant that has
    # simply not connected a cost connector yet would be the most flattering
    # and least true thing this function could return.
    roi_multiplier: Decimal | None = None
    if spend > 0:
        roi_multiplier = (human_value / spend).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return RoiResult(
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
        ai_spend_usd=spend,
        hours_saved_per_month=hours_saved.hours_per_month,
        hours_saved_in_window=hours_in_window,
        blended_hourly_rate_usd=blended_hourly_rate_usd,
        human_value_usd=human_value,
        net_value_usd=net_value,
        roi_multiplier=roi_multiplier,
        basis=hours_saved.basis,
        basis_detail=hours_saved.detail,
        # The invariant the UI badge depends on: anything not grounded in this
        # tenant's own measured usage is flagged, without exception.
        is_illustrative=hours_saved.basis is not HoursSavedBasis.measured,
    )


async def get_or_default_assumptions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> RoiAssumptions:
    """The tenant's assumptions, or an unsaved row carrying the defaults.

    Returns a transient instance rather than creating one, so that merely
    *viewing* the ROI page does not write a row that then looks, in an audit,
    like somebody configured something.
    """
    existing = (
        await db.execute(select(RoiAssumptions).where(RoiAssumptions.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    return RoiAssumptions(
        tenant_id=tenant_id,
        blended_hourly_rate_usd=DEFAULT_BLENDED_HOURLY_RATE_USD,
        hours_saved_source=HoursSavedSource.adoption_pipeline,
        manual_hours_saved_per_month=None,
    )


async def ledger_spend_usd(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: date,
    until: date,
) -> Decimal:
    """Total ledger spend for the window — the measured half of the ratio.

    Filtered on `tenant_id` explicitly rather than relying on RLS alone, per
    the repo's tenancy rule. Reading another tenant's spend here would not just
    leak it, it would silently corrupt this tenant's headline number.
    """
    total = (
        await db.execute(
            select(func.coalesce(func.sum(AICostRecord.cost_usd), 0)).where(
                AICostRecord.tenant_id == tenant_id,
                AICostRecord.usage_date >= since,
                AICostRecord.usage_date <= until,
            )
        )
    ).scalar_one()
    return Decimal(str(total))


async def build_roi(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: date,
    until: date,
) -> RoiResult:
    """Assemble the ROI view for one tenant and window."""
    assumptions = await get_or_default_assumptions(db, tenant_id)
    spend = await ledger_spend_usd(db, tenant_id=tenant_id, since=since, until=until)
    hours = resolve_hours_saved(assumptions)

    return compute_roi(
        window_start=since,
        window_end=until,
        ai_spend_usd=spend,
        hours_saved=hours,
        blended_hourly_rate_usd=Decimal(str(assumptions.blended_hourly_rate_usd)),
    )
