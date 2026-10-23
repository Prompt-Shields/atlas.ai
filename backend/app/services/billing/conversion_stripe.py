"""The read-only Stripe pass of the conversion report (promptly-guide#43).

promptly-guide's ``docs/conversion-metrics.md`` defines the funnel, and its
decision 9 has the report run once a month by whoever holds a read-only live
key. ``/api/v1/admin/conversion`` answers what Atlas holds; its
``not_from_atlas`` names what only Stripe holds. This module reads those from
Stripe, per company, and joins them to the report's rows:

  * **Converted**: the first module sale. The earliest ``created`` of any
    subscription item carrying a module price, on any of the customer's
    subscriptions. A module item removed later is gone from its subscription,
    so the invoices that billed a module are read too, and the earlier date
    wins.
  * **Quoted**: the earliest ``status_transitions.finalized_at`` of a quote
    with ``metadata.billing_kind = enterprise_modules`` (atlas.ai#14).
  * **Paid**: the earliest ``status_transitions.paid_at`` of a paid invoice
    that carries a module.
  * **Pilot**: ``metadata.promptly_pilot`` on the Work client subscription.
    ``enterprise.end_pilot`` sets it to ``"false"`` when a pilot converts, so
    this is the flag now, not "was a pilot"; the column says so.
  * **Day 0 in Stripe**: the Work client subscription's ``start_date``, a
    cross-check of ``tenants.work_client_started_at``, and its ``ended_at``.

Then, per company: days from day 0 to the first sale, whether that sale fell
within six calendar months of day 0, and whether it came before day 0 (an
existing customer, reported apart and left out of the rate).

And per cohort month, the note's one table (``cohorts``): started, pilots,
connected, quoted, converted (any module and Suite), paid, lost, the 90-day
rule, seats per converted company, the median days to convert and from
connected to quoted, and counts without a rate below ten companies.

Read-only by construction: the only Stripe calls are ``list_subscriptions``,
``list_invoices`` and ``list_all_quotes`` in ``stripe_client``. Nothing is
written to Stripe or to the database, and nothing is stored. Every figure is
about the company: dates, module names and the seat quantity it bought. No
amount is read, and no person, adoption figure or usage record is touched.

A module is recognised by its price's lookup key, or failing that by its
product id (``catalog.PRODUCTS`` ids are fixed). The product matters: when an
amount changes the catalogue moves the lookup key to a new price, and older
prices still on a subscription or an invoice keep only the product. Invoice
lines carry ``price`` on API versions before 2025-03-31 (the SDK pinned here
is on 2024-06-20) and ``pricing.price_details`` from then on; both are read.
"""

from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.services.billing import catalog, enterprise, quotes, stripe_client
from app.services.conversion_report import (
    MIN_COMPANIES_FOR_RATE,
    WINDOW_MONTHS,
    CompanyRow,
    add_months,
)

# Lookup key -> product id, for the paid modules and the Work client.
_PRODUCT_FOR_KEY = {p.lookup_key: p.product_id for p in catalog.PRICES}
_MODULE_KEYS = frozenset(catalog.MODULE_PRICES.values())
_KEY_FOR_PRODUCT = {_PRODUCT_FOR_KEY[k]: k for k in (*sorted(_MODULE_KEYS), catalog.WORK_CLIENT)}

# Invoices in these states were never a sale.
_NOT_A_SALE = frozenset({"draft", "void"})

# The Stripe reads this pass makes, per customer. Printed by the dry run.
READS: tuple[str, ...] = (
    "Subscription.list(customer=<id>, status='all')",
    "Invoice.list(customer=<id>)",
    "Quote.list(customer=<id>)",
)


def _get(obj, key: str, default=None):
    """A field of a Stripe object or a plain dict, or ``default``."""
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


def _id(obj) -> str | None:
    """An expandable field: an id string, or an object with an id."""
    if isinstance(obj, str):
        return obj
    return _get(obj, "id")


def _when(ts) -> datetime | None:
    return datetime.fromtimestamp(ts, UTC) if ts else None


def _earliest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def _key_of(price, product=None) -> str | None:
    """The catalogue lookup key this price or product stands for, if any."""
    key = _get(price, "lookup_key")
    if key in _MODULE_KEYS or key == catalog.WORK_CLIENT:
        return key
    return _KEY_FOR_PRODUCT.get(_id(product) or _id(_get(price, "product")))


def _line_key(line) -> str | None:
    """A module or Work client key for an invoice line, on old or new API versions."""
    price = _get(line, "price")
    if price is not None:
        return _key_of(price)
    details = _get(_get(line, "pricing", {}), "price_details", {})
    return _key_of(None, _get(details, "product"))


@dataclass(frozen=True)
class Sale:
    when: datetime
    module: str  # a module lookup key
    seats: int | None


@dataclass
class StripeFacts:
    """What Stripe says about one customer. Every field is about the company."""

    customer: str
    stripe_day0: datetime | None = None
    work_client_ended_at: datetime | None = None
    pilot_now: bool | None = None
    quoted_at: datetime | None = None
    first_sale: Sale | None = None
    first_suite_sold_at: datetime | None = None
    paid_at: datetime | None = None
    # Reads Stripe cut short (an embedded list with more pages), so a date may
    # be late. Named in the output rather than hidden.
    warnings: list[str] = field(default_factory=list)


def _note_sale(facts: StripeFacts, sale: Sale) -> None:
    if facts.first_sale is None or sale.when < facts.first_sale.when:
        facts.first_sale = sale
    if sale.module == catalog.SUITE_ANNUAL:
        facts.first_suite_sold_at = _earliest(facts.first_suite_sold_at, sale.when)


def _read_subscriptions(facts: StripeFacts, subscriptions: Iterable) -> None:
    work_client = []
    for sub in subscriptions:
        items = _get(sub, "items", {})
        if _get(items, "has_more"):
            facts.warnings.append(f"{_id(sub)}: more than one page of items")
        for item in _get(items, "data", []):
            key = _key_of(_get(item, "price"))
            if key == catalog.WORK_CLIENT:
                work_client.append(sub)
            elif key in _MODULE_KEYS and _get(item, "created"):
                _note_sale(facts, Sale(_when(item["created"]), key, _get(item, "quantity")))
    if work_client:
        # The first Work client subscription sets day 0. It ended only if every
        # one has ended.
        first = min(work_client, key=lambda s: _get(s, "start_date", 0))
        facts.stripe_day0 = _when(_get(first, "start_date"))
        facts.pilot_now = enterprise.is_pilot(first)
        ends = [_get(s, "ended_at") for s in work_client]
        if all(ends):
            facts.work_client_ended_at = _when(max(ends))


def _read_invoices(facts: StripeFacts, invoices: Iterable) -> None:
    for invoice in invoices:
        if _get(invoice, "status") in _NOT_A_SALE:
            continue
        lines = _get(invoice, "lines", {})
        if _get(lines, "has_more"):
            facts.warnings.append(f"{_id(invoice)}: more than one page of lines")
        modules = [
            (key, _get(line, "quantity"))
            for line in _get(lines, "data", [])
            if (key := _line_key(line)) in _MODULE_KEYS
        ]
        if not modules:
            continue
        created = _when(_get(invoice, "created"))
        if created:
            for key, seats in modules:
                _note_sale(facts, Sale(created, key, seats))
        if _get(invoice, "status") == "paid":
            paid = _when(_get(_get(invoice, "status_transitions", {}), "paid_at"))
            facts.paid_at = _earliest(facts.paid_at, paid)


def _read_quotes(facts: StripeFacts, listed: Iterable) -> None:
    for quote in listed:
        meta = _get(quote, "metadata", {})
        if _get(meta, quotes.BILLING_KIND_KEY) != quotes.BILLING_KIND:
            continue
        finalized = _when(_get(_get(quote, "status_transitions", {}), "finalized_at"))
        facts.quoted_at = _earliest(facts.quoted_at, finalized)


def read_customer(customer: str) -> StripeFacts:
    """Every Stripe fact the report needs about one customer. Reads only."""
    facts = StripeFacts(customer=customer)
    _read_subscriptions(facts, stripe_client.list_subscriptions(customer=customer, status="all"))
    _read_invoices(facts, stripe_client.list_invoices(customer=customer))
    _read_quotes(facts, stripe_client.list_all_quotes(customer=customer))
    return facts


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------

COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "name",
    "cohort",
    "day0",
    "stripe_day0",
    "pilot_now",
    "guide_connected_at",
    "quoted_at",
    "first_module_sold_at",
    "first_module",
    "first_module_seats",
    "first_suite_sold_at",
    "paid_at",
    "days_to_convert",
    "converted_within_window",
    "existing_customer",
    "paid_module_now",
    "suite_now",
    "seats_now",
    "lost",
    "work_client_ended_at",
    "window_open",
    "stripe",
)

_MODULE_NAME = {key: name for name, key in catalog.MODULE_PRICES.items()}


@dataclass(frozen=True)
class JoinedRow:
    company: CompanyRow
    facts: StripeFacts | None

    @property
    def day0(self) -> datetime | None:
        """Atlas's day 0, or Stripe's when Atlas has none."""
        if self.company.work_client_started_at is not None:
            return self.company.work_client_started_at
        return self.facts.stripe_day0 if self.facts else None

    @property
    def cohort(self) -> str | None:
        """The month of day 0 (UTC): Atlas's, or Stripe's when Atlas has none."""
        if self.company.cohort is not None:
            return self.company.cohort
        day0 = self.day0
        return day0.astimezone(UTC).strftime("%Y-%m") if day0 else None

    @property
    def first_sale(self) -> Sale | None:
        return self.facts.first_sale if self.facts else None

    @property
    def lost(self) -> bool:
        """The Work client ended with no module ever sold.

        From Stripe when it was read, since it also sees a module item that
        was removed before the end; from Atlas's current state otherwise.
        """
        if self.facts is None:
            return self.company.lost
        return self.facts.work_client_ended_at is not None and self.facts.first_sale is None

    @property
    def existing_customer(self) -> bool | None:
        """A module sold before day 0: an Atlas.AI customer adding the client."""
        if self.first_sale is None or self.day0 is None:
            return None
        return self.first_sale.when < self.day0

    @property
    def days_to_convert(self) -> int | None:
        if self.first_sale is None or self.day0 is None or self.existing_customer:
            return None
        return (self.first_sale.when - self.day0).days

    @property
    def converted_within_window(self) -> bool | None:
        """Sold on or after day 0 and within six calendar months of it."""
        if self.day0 is None or self.facts is None:
            return None
        sale = self.first_sale
        if sale is None or self.existing_customer:
            return False
        return sale.when < add_months(self.day0, WINDOW_MONTHS)

    @property
    def suite_within_window(self) -> bool | None:
        """The Suite sold on or after day 0 and within six calendar months of it."""
        within = self.converted_within_window
        if not within:
            return within
        suite = self.facts.first_suite_sold_at
        return suite is not None and self.day0 <= suite < add_months(self.day0, WINDOW_MONTHS)

    @property
    def days_connected_to_quoted(self) -> int | None:
        """Days from Guide connected to the first module quote, when it came after."""
        connected, quoted = self.company.guide_connected_at, self.facts and self.facts.quoted_at
        if connected is None or not quoted or quoted < connected:
            return None
        return (quoted - connected).days

    def as_csv(self) -> dict[str, str]:
        c, f, sale = self.company, self.facts, self.first_sale
        values = {
            "tenant_id": c.tenant_id,
            "name": c.name,
            "cohort": self.cohort,
            "day0": self.day0,
            "stripe_day0": f and f.stripe_day0,
            "pilot_now": f and f.pilot_now,
            "guide_connected_at": c.guide_connected_at,
            "quoted_at": f and f.quoted_at,
            "first_module_sold_at": sale and sale.when,
            "first_module": sale and _MODULE_NAME[sale.module],
            "first_module_seats": sale and sale.seats,
            "first_suite_sold_at": f and f.first_suite_sold_at,
            "paid_at": f and f.paid_at,
            "days_to_convert": self.days_to_convert,
            "converted_within_window": self.converted_within_window,
            "existing_customer": self.existing_customer,
            "paid_module_now": c.paid_module_now,
            "suite_now": c.suite_now,
            "seats_now": c.seats_now,
            "lost": self.lost,
            "work_client_ended_at": f and f.work_client_ended_at,
            "window_open": c.window_open,
            "stripe": _stripe_status(c, f),
        }
        return {k: _cell(v) for k, v in values.items()}


def _stripe_status(company: CompanyRow, facts: StripeFacts | None) -> str:
    if not company.stripe_customer_id:
        return "no customer"
    if facts is None:
        return "not read"
    if facts.warnings:
        return "read, partial: " + "; ".join(facts.warnings)
    return "read"


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.astimezone(UTC).date().isoformat()
    return str(value)


def join(companies: Iterable[CompanyRow], facts: Mapping[str, StripeFacts]) -> list[JoinedRow]:
    """The report's rows, in its order, each with its customer's Stripe facts."""
    return [
        JoinedRow(c, facts.get(c.stripe_customer_id) if c.stripe_customer_id else None)
        for c in companies
    ]


def read_all(companies: Iterable[CompanyRow]) -> dict[str, StripeFacts]:
    """Stripe facts for every company that has a Stripe customer, read once each."""
    customers = dict.fromkeys(c.stripe_customer_id for c in companies if c.stripe_customer_id)
    return {customer: read_customer(customer) for customer in customers}


def to_csv(rows: Iterable[JoinedRow]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.as_csv())
    return out.getvalue()


# ---------------------------------------------------------------------------
# The cohort table
# ---------------------------------------------------------------------------

# The note's one table ("The monthly report"), one row per cohort month and a
# last row for all of them. Every figure is a count of companies, a median of
# their dates or seats, or a share of companies.
COHORT_COLUMNS: tuple[str, ...] = (
    "cohort",
    "started",
    "pilots_now",
    "connected",
    "quoted",
    "converted_any_module",
    "converted_suite",
    "paid",
    "lost",
    "endpoint_required",
    "not_measured",
    "rate_any_module",
    "rate_suite",
    "median_seats_converted",
    "median_days_to_convert",
    "median_days_connected_to_quoted",
    "existing_customers",
    "window",
)

ALL_COHORTS = "all"


@dataclass(frozen=True)
class CohortFunnel:
    """One cohort's funnel. Existing customers are counted apart, in no stage.

    ``started`` is the denominator: the cohort's companies less the existing
    customers. ``converted_*`` count a first sale within six calendar months
    of day 0. ``not_measured`` are in ``started`` but could not be measured
    against Stripe (no customer, not read, or no day 0 anywhere), so they are
    in no Stripe stage. The rates stay empty below ``MIN_COMPANIES_FOR_RATE``
    companies, or while any company is not measured: read the counts.
    """

    cohort: str
    started: int
    pilots_now: int
    connected: int
    quoted: int
    converted_any_module: int
    converted_suite: int
    paid: int
    lost: int
    endpoint_required: int
    not_measured: int
    rate_any_module: float | None
    rate_suite: float | None
    median_seats_converted: float | None
    median_days_to_convert: float | None
    median_days_connected_to_quoted: float | None
    existing_customers: int
    window: str  # "so far" while any company's six months are open, else "closed"

    def as_csv(self) -> dict[str, str]:
        return {name: _cell(getattr(self, name)) for name in COHORT_COLUMNS}


def _median(values: Iterable[int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None


def _share(count: int, total: int, not_measured: int) -> float | None:
    if total < MIN_COMPANIES_FOR_RATE or not_measured:
        return None
    return round(count / total, 3)


def _funnel(cohort: str, rows: list[JoinedRow]) -> CohortFunnel:
    existing = [r for r in rows if r.existing_customer]
    counted = [r for r in rows if not r.existing_customer]
    converted = [r for r in counted if r.converted_within_window]
    not_measured = sum(r.converted_within_window is None for r in counted)
    suite = sum(bool(r.suite_within_window) for r in counted)
    return CohortFunnel(
        cohort=cohort,
        started=len(counted),
        pilots_now=sum(bool(r.facts and r.facts.pilot_now) for r in counted),
        connected=sum(r.company.guide_connected_at is not None for r in counted),
        quoted=sum(bool(r.facts and r.facts.quoted_at) for r in counted),
        converted_any_module=len(converted),
        converted_suite=suite,
        paid=sum(bool(r.facts and r.facts.paid_at) for r in counted),
        lost=sum(r.lost for r in counted),
        endpoint_required=sum(r.company.free_client_status == "endpoint_required" for r in counted),
        not_measured=not_measured,
        rate_any_module=_share(len(converted), len(counted), not_measured),
        rate_suite=_share(suite, len(counted), not_measured),
        median_seats_converted=_median(r.first_sale.seats for r in converted),
        median_days_to_convert=_median(r.days_to_convert for r in converted),
        median_days_connected_to_quoted=_median(r.days_connected_to_quoted for r in counted),
        existing_customers=len(existing),
        window="so far" if any(r.company.window_open for r in rows) else "closed",
    )


def cohorts(rows: Iterable[JoinedRow]) -> list[CohortFunnel]:
    """The funnel per cohort month, in order, then one row for all cohorts.

    Companies with no day 0 anywhere come last before the total, as cohort "".
    """
    rows = list(rows)
    by_cohort: dict[str, list[JoinedRow]] = defaultdict(list)
    for row in rows:
        by_cohort[row.cohort or ""].append(row)
    ordered = sorted(by_cohort, key=lambda c: (c == "", c))
    return [_funnel(c, by_cohort[c]) for c in ordered] + [_funnel(ALL_COHORTS, rows)]


def cohorts_to_csv(funnels: Iterable[CohortFunnel]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COHORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for funnel in funnels:
        writer.writerow(funnel.as_csv())
    return out.getvalue()
