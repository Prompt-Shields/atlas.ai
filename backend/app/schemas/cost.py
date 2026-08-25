"""Pydantic schemas for the cost router."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


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
