"""Free-to-paid conversion, from billing and tenant records only (promptly-guide#43).

The definitions are promptly-guide's `docs/conversion-metrics.md`. Conversion is
measured from what a company bought and when, never from what anyone did in
Guide. This report is the note's Atlas read: one row per company on the Work
client, and those rows counted by the month the Work client started.

It reads three things Atlas already holds, all about the company:

  * `tenants.work_client_started_at`: day 0, which sets the cohort month;
  * `guide_connections.created_at`: when Guide was connected (one per tenant);
  * `tenants.stripe_subscriptions`: the module subscriptions as the webhooks
    last saw them, which say who is paying for a module now and for how many
    seats.

It never reads a user, an adoption figure (`guide_adoption_*`), Atlas's trial
telemetry (`trial_events`) or anything Guide sends. The only figure about
people is the seat quantity the company bought.

What it cannot say: Atlas keeps no history of sales, only the current state of
each subscription. When a module was first sold, whether its first invoice
was paid, whether a quote was sent, whether the company is a pilot, and
whether it already paid for a module before day 0 all live in Stripe. Until
they are read from there (the note's decision 5), the cohort figure is "paying
for a module now", not "converted within six months", and the two differ for
a company that bought and then cancelled. `NOT_FROM_ATLAS` says so in every
response.
"""

from __future__ import annotations

import calendar
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guide_adoption import GuideConnection
from app.models.tenant import Tenant
from app.services.billing import catalog
from app.services.entitlements import free_client_grace

# Below this many companies a cohort shows counts and no rate. The first pilot
# has three: "1 of 3" is a fact, "33%" looks like a finding.
MIN_COMPANIES_FOR_RATE = 10

# The plan's window: conversion within six calendar months of day 0.
WINDOW_MONTHS = 6

# Stripe statuses that mean the subscription is on now, as in the note's SQL.
# A module whose invoice failed (past_due) is not "paid now".
_LIVE = frozenset({"active", "trialing"})
_ENDED = frozenset({"canceled", "incomplete_expired"})

_PAID_MODULE_KEYS = frozenset(catalog.MODULE_PRICES.values())

# The note's stages and figures that need Stripe, with the reason. Returned
# with the report, so nobody reads "paying now" as the six-month rate.
NOT_FROM_ATLAS: dict[str, str] = {
    "converted_at": "When the first module was sold: the earliest module item's "
    "`created` in Stripe. Atlas keeps current state only.",
    "time_to_convert": "Days from Started to Converted. Needs converted_at.",
    "quoted": "A finalised module quote: Stripe Quote `finalized_at`.",
    "paid": "The first module invoice paid: Stripe invoice `paid_at`.",
    "pilot": "The pilot flag: Stripe subscription metadata `promptly_pilot`.",
    "existing_customer": "A module sold before day 0. Needs converted_at.",
}


@dataclass(frozen=True)
class CompanyRow:
    """One company on the Work client. Every field is about the company."""

    tenant_id: uuid.UUID
    name: str
    stripe_customer_id: str | None
    cohort: str | None  # "2026-07" (UTC), or None when day 0 is not recorded
    work_client_started_at: datetime | None
    guide_connected_at: datetime | None
    work_client_live: bool
    paid_module_now: bool
    suite_now: bool
    seats_now: int | None  # seats on the paid modules on now, counted once
    lost: bool
    free_client_status: str | None
    window_open: bool  # fewer than six calendar months since day 0


@dataclass(frozen=True)
class CohortRow:
    cohort: str | None
    started: int
    connected: int
    paying_now_any_module: int
    paying_now_suite: int
    lost: int
    endpoint_required: int
    # None until the cohort has MIN_COMPANIES_FOR_RATE companies.
    paying_now_rate_any_module: float | None
    paying_now_rate_suite: float | None
    median_seats_paying: float | None
    window: str  # "so far" while any company's six months are open, else "closed"


@dataclass(frozen=True)
class ConversionReport:
    generated_at: datetime
    companies: list[CompanyRow]
    cohorts: list[CohortRow]
    not_from_atlas: dict[str, str]


def add_months(when: datetime, months: int) -> datetime:
    """`when` plus calendar months, the day clamped to the month's end."""
    index = when.month - 1 + months
    year, month = when.year + index // 12, index % 12 + 1
    days = calendar.monthrange(year, month)[1]
    return when.replace(year=year, month=month, day=min(when.day, days))


def _subscriptions(tenant: Tenant) -> list[dict]:
    return list((tenant.stripe_subscriptions or {}).values())


def _items(sub: dict) -> dict:
    return sub.get("items") or {}


def _on_work_client(tenant: Tenant, connected: bool) -> bool:
    """The note's filter: day 0 recorded, Guide connected, or a Work client item."""
    return (
        tenant.work_client_started_at is not None
        or connected
        or any(catalog.WORK_CLIENT in _items(s) for s in _subscriptions(tenant))
    )


def company_row(
    tenant: Tenant, guide_connected_at: datetime | None, *, now: datetime
) -> CompanyRow:
    subs = _subscriptions(tenant)
    live = [s for s in subs if s.get("status") in _LIVE]
    work_client_subs = [s for s in subs if catalog.WORK_CLIENT in _items(s)]

    live_module_items = [
        (key, qty) for s in live for key, qty in _items(s).items() if key in _PAID_MODULE_KEYS
    ]
    # A seat with Atlas.AI and Learn is one seat: take the largest quantity,
    # not the sum.
    quantities = [qty for _, qty in live_module_items if isinstance(qty, int)]
    # "Ever sold", as far as Atlas can tell: an ended subscription keeps the
    # items it last had. A module item removed before the end is not seen.
    module_on_record = any(key in _PAID_MODULE_KEYS for s in subs for key in _items(s))

    started = tenant.work_client_started_at
    grace = free_client_grace(tenant, now=now)
    return CompanyRow(
        tenant_id=tenant.id,
        name=tenant.name,
        stripe_customer_id=tenant.stripe_customer_id,
        cohort=started.astimezone(UTC).strftime("%Y-%m") if started else None,
        work_client_started_at=started,
        guide_connected_at=guide_connected_at,
        work_client_live=any(catalog.WORK_CLIENT in _items(s) for s in live),
        paid_module_now=bool(live_module_items),
        suite_now=any(key == catalog.SUITE_ANNUAL for key, _ in live_module_items),
        seats_now=max(quantities) if quantities else None,
        lost=bool(work_client_subs)
        and all(s.get("status") in _ENDED for s in work_client_subs)
        and not module_on_record,
        free_client_status=grace.status.value if grace.status else None,
        window_open=started is None or add_months(started, WINDOW_MONTHS) > now,
    )


def _rate(count: int, total: int) -> float | None:
    if total < MIN_COMPANIES_FOR_RATE:
        return None
    return round(count / total, 3)


def cohort_rows(companies: list[CompanyRow]) -> list[CohortRow]:
    by_cohort: dict[str | None, list[CompanyRow]] = defaultdict(list)
    for row in companies:
        by_cohort[row.cohort].append(row)

    out = []
    # Months in order; companies with no recorded day 0 last.
    for cohort in sorted(by_cohort, key=lambda c: (c is None, c or "")):
        rows = by_cohort[cohort]
        paying = [r for r in rows if r.paid_module_now]
        suite = [r for r in rows if r.suite_now]
        seats = [r.seats_now for r in paying if r.seats_now is not None]
        out.append(
            CohortRow(
                cohort=cohort,
                started=len(rows),
                connected=sum(r.guide_connected_at is not None for r in rows),
                paying_now_any_module=len(paying),
                paying_now_suite=len(suite),
                lost=sum(r.lost for r in rows),
                endpoint_required=sum(r.free_client_status == "endpoint_required" for r in rows),
                paying_now_rate_any_module=_rate(len(paying), len(rows)),
                paying_now_rate_suite=_rate(len(suite), len(rows)),
                median_seats_paying=statistics.median(seats) if seats else None,
                window="so far" if any(r.window_open for r in rows) else "closed",
            )
        )
    return out


async def build_report(db: AsyncSession) -> ConversionReport:
    """The report across all tenants. Super admin only: it spans tenants.

    Test tenants are left out (`is_test_data`), as the note asks.
    """
    now = await db.scalar(select(func.now()))
    connected_at = dict(
        (await db.execute(select(GuideConnection.tenant_id, GuideConnection.created_at))).all()
    )
    tenants = (await db.scalars(select(Tenant).where(Tenant.is_test_data.is_(False)))).all()

    companies = [
        company_row(t, connected_at.get(t.id), now=now)
        for t in tenants
        if _on_work_client(t, t.id in connected_at)
    ]
    companies.sort(
        key=lambda r: (r.work_client_started_at is None, r.work_client_started_at or now, r.name)
    )
    return ConversionReport(
        generated_at=now,
        companies=companies,
        cohorts=cohort_rows(companies),
        not_from_atlas=dict(NOT_FROM_ATLAS),
    )
