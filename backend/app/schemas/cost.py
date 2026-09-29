"""Pydantic schemas for the cost router."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai_cost_record import CostProvider, SelfHostedCostProvider
from app.models.cost_budget import (
    DEFAULT_WARN_THRESHOLD_PERCENT,
    BudgetAlertLevel,
)
from app.models.roi_assumptions import HoursSavedSource
from app.services.cost.roi import HoursSavedBasis


class SyncResponse(BaseModel):
    """Outcome of a single manual "Sync now" call.

    ``status`` is the ``IntegrationStatus`` enum value as a string (e.g.
    ``"CONNECTED"`` / ``"ERROR"``).
    """

    records_upserted: int
    since: date
    until: date
    status: str
    error: str | None = None


class CronSyncItem(BaseModel):
    """Per-integration summary inside a cron sweep."""

    integration_id: str
    status: str
    records_upserted: int
    error: str | None = None


class CronSyncResponse(BaseModel):
    """Aggregate result of the all-tenant cron sweep."""

    integrations_synced: int
    results: list[CronSyncItem]


class SummaryResponse(BaseModel):
    """Roll-up of cost over a date window for the caller's tenant.

    ``vendor_reported_usd`` + ``derived_usd`` partitions ``total_cost_usd`` by
    ``cost_source``. ``provisional_usd`` overlaps the others (it is the subset
    of spend still being restated). ``active_connectors`` is the count of
    distinct integrations that produced any matching row.
    """

    total_cost_usd: Decimal
    vendor_reported_usd: Decimal
    derived_usd: Decimal
    provisional_usd: Decimal
    active_connectors: int


class TimeseriesPoint(BaseModel):
    """One daily-grain spend bucket. ``is_provisional`` is true if ANY row
    that day is still provisional."""

    date: date
    cost_usd: Decimal
    is_provisional: bool


class BreakdownRow(BaseModel):
    """One grouped spend bucket. ``cost_source`` is the group's dominant
    source, or ``"mixed"`` when the group spans more than one source."""

    key: str
    cost_usd: Decimal
    cost_source: str


# ── Self-hosted usage push (slice 2) ─────────────────────────────────


class SelfHostedUsageRecord(BaseModel):
    """One model call reported by a customer's own instrumented app.

    Deliberately narrow: tokens and a model name, not dollars. The customer's
    spend for these calls sits on their own cloud bill, so we derive cost from
    a price book and tag it `derived_tokens` — see
    `app.services.cost.price_book`.

    `extra="forbid"` mirrors the prompt-telemetry contract: an unrecognised
    field is a client bug worth surfacing, not something to silently drop, and
    it forecloses a future where prompt text arrives here by accident.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1, max_length=200)
    tokens_in: int = Field(0, ge=0)
    tokens_out: int = Field(0, ge=0)
    # When the call happened. Naive timestamps are read as UTC; the ledger's
    # grain is a UTC day.
    occurred_at: datetime | None = None
    # Optional free-form app/deployment label, kept in raw_metadata for the
    # customer's own attribution. Never used as a ledger key.
    app_id: str | None = Field(None, max_length=120)

    @field_validator("model")
    @classmethod
    def _strip_model(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("model must not be blank")
        return stripped


class SelfHostedUsageBatch(BaseModel):
    """A push of usage records, idempotent on `batch_id`."""

    model_config = ConfigDict(extra="forbid")

    # Client-supplied idempotency key. Required, because accumulation is not
    # idempotent: without it a retried batch double-counts silently.
    batch_id: str = Field(..., min_length=1, max_length=200)
    provider: SelfHostedCostProvider
    records: list[SelfHostedUsageRecord] = Field(..., min_length=1, max_length=1000)


class SelfHostedUsageIngestResponse(BaseModel):
    """What the push did.

    `unpriced_models` is the field worth watching: those calls were counted and
    their tokens recorded, but contributed no cost because the price book has
    no entry. Silence there would understate the customer's spend.
    """

    batch_id: str
    accepted_calls: int
    skipped_calls: int
    rows_touched: int
    cost_usd: Decimal
    unpriced_models: list[str] = Field(default_factory=list)
    duplicate: bool = False


# ─── ROI (cost-ledger slice 3) ───────────────────────────────────────


class RoiAssumptionsPayload(BaseModel):
    """The tenant's human-cost model, as read back."""

    blended_hourly_rate_usd: Decimal
    hours_saved_source: HoursSavedSource
    manual_hours_saved_per_month: Decimal | None = None
    updated_at: datetime | None = None
    updated_by_user_id: str | None = None
    # True when the tenant has never saved a model and is seeing the defaults.
    # Without it the UI cannot distinguish "we chose $75" from "nobody has set
    # this", and those warrant different prompts.
    is_default: bool = False


class RoiAssumptionsUpdate(BaseModel):
    """An admin's edit to the human-cost model.

    ``extra="forbid"`` for the same reason the usage ingest forbids it: a
    misspelled field silently ignored would leave the admin believing they had
    changed the number behind their ROI headline.
    """

    model_config = ConfigDict(extra="forbid")

    # Upper bound is a typo guard, not a policy: $10,000/h is far outside any
    # real loaded rate, and an accidental extra zero would inflate the
    # headline rather than error.
    blended_hourly_rate_usd: Decimal = Field(..., gt=0, le=10_000)
    hours_saved_source: HoursSavedSource
    manual_hours_saved_per_month: Decimal | None = Field(default=None, ge=0)


class RoiResponse(BaseModel):
    """AI ROI over a window.

    Half of this is measured and half is estimated, and the split is explicit
    rather than implied: ``ai_spend_usd`` is the ledger's own total, while
    ``human_value_usd`` is ``hours_saved x blended_hourly_rate`` and inherits
    the uncertainty of both.

    ``roi_multiplier`` is null when there was no spend in the window. That is
    not a missing value to paper over — with a zero denominator the ratio is
    undefined, and rendering it as "infinite ROI" would be this endpoint's most
    flattering and least true output.
    """

    window_start: date
    window_end: date
    window_days: int

    ai_spend_usd: Decimal

    hours_saved_per_month: Decimal
    hours_saved_in_window: Decimal
    blended_hourly_rate_usd: Decimal
    human_value_usd: Decimal

    net_value_usd: Decimal
    roi_multiplier: Decimal | None = None

    basis: HoursSavedBasis
    basis_detail: str
    # Set whenever the hours-saved input is not this tenant's own measured
    # usage. The UI is expected to badge it; the API states it either way so a
    # consumer cannot accidentally present an illustration as a finding.
    is_illustrative: bool


class BudgetPayload(BaseModel):
    """A budget as the API returns it, with this month's standing attached.

    The status fields are folded into the same object rather than served from a
    separate endpoint because they are never wanted apart: a ceiling without
    the spend beside it is a number the reader has to go and contextualise, and
    the whole point of this slice is to stop making them do that.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: CostProvider | None
    amount_usd: Decimal
    warn_threshold_percent: Decimal
    alerts_enabled: bool

    # ─── this month's standing ────────────────────────────────────
    period_start: date
    as_of: date
    spend_usd: Decimal
    provisional_usd: Decimal
    percent_used: Decimal | None
    alert_level: BudgetAlertLevel

    # False when the tenant has no cost rows this month at all. The UI must
    # read this before rendering a low percentage as reassurance: "0% used"
    # and "we have no data" look identical in the number alone.
    has_ledger_data: bool

    # None until enough of the month has elapsed to extrapolate honestly.
    projected_month_end_usd: Decimal | None

    last_alerted_at: datetime | None


class BudgetUpsert(BaseModel):
    """Create or replace the budget for one scope.

    ``extra="forbid"`` matching the ROI assumptions update: a misspelled field
    silently dropped would leave an admin believing they had set a ceiling that
    does not exist, and they would find out from an invoice.
    """

    model_config = ConfigDict(extra="forbid")

    # None targets the tenant-wide budget. Explicit in the body rather than a
    # path parameter so "the overall budget" does not need a magic path
    # segment like /budgets/_all.
    provider: CostProvider | None = None

    # Upper bound is a typo guard rather than a policy, as on the ROI rate:
    # an accidental extra zero should not silently raise the ceiling tenfold.
    amount_usd: Decimal = Field(..., gt=0, le=10_000_000)

    # Mirrors the DB CHECK. A threshold of 0 would warn before a cent was
    # spent; above 100 it could never fire, which is a setting that quietly
    # does nothing.
    warn_threshold_percent: Decimal = Field(default=DEFAULT_WARN_THRESHOLD_PERCENT, gt=0, le=100)

    alerts_enabled: bool = True
