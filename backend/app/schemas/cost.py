"""Pydantic schemas for the cost router."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai_cost_record import SelfHostedCostProvider


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
