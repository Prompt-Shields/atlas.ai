"""Unit tests for the read-only Stripe pass of the conversion report (promptly-guide#43).

Stripe is replaced by an in-memory fake patched over the ``stripe_client``
wrappers, as in ``test_billing_portal.py``. Every other wrapper raises, so a
write would fail the test. No amounts appear here.
"""

from __future__ import annotations

import ast
import csv
import inspect
import io
import uuid
from datetime import UTC, datetime

import pytest

from app.models.tenant import Plan, Tenant
from app.services import conversion_report as cr
from app.services.billing import conversion_stripe as cs
from app.services.billing import stripe_client
from tests.conftest import TestSessionLocal, engine

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
JUL_3 = datetime(2026, 7, 3, tzinfo=UTC)

READS = ("list_subscriptions", "list_invoices", "list_all_quotes")


def ts(*args) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def price(key: str | None, product: str | None = None) -> dict:
    return {"id": f"price_{key or product}", "lookup_key": key, "product": product}


def item(key: str | None, created: int, quantity: int = 1, product: str | None = None) -> dict:
    return {
        "id": f"si_{key}_{created}",
        "price": price(key, product),
        "created": created,
        "quantity": quantity,
    }


def sub(*items, start: int, ended: int | None = None, pilot: str | None = None, **kw) -> dict:
    meta = {"tenant_id": "t"}
    if pilot is not None:
        meta["promptly_pilot"] = pilot
    return {
        "id": kw.get("id", f"sub_{start}"),
        "start_date": start,
        "ended_at": ended,
        "metadata": meta,
        "items": {"data": list(items), "has_more": kw.get("more", False)},
    }


def invoice(*lines, created: int, status: str = "paid", paid: int | None = None) -> dict:
    return {
        "id": f"in_{created}",
        "status": status,
        "created": created,
        "status_transitions": {"paid_at": paid},
        "lines": {"data": list(lines)},
    }


def quote(finalized: int | None, kind: str = "enterprise_modules") -> dict:
    return {
        "id": f"qt_{finalized}",
        "metadata": {"billing_kind": kind},
        "status_transitions": {"finalized_at": finalized},
    }


class FakeStripe:
    def __init__(self) -> None:
        self.subscriptions: dict[str, list] = {}
        self.invoices: dict[str, list] = {}
        self.quotes: dict[str, list] = {}
        self.reads: list[tuple[str, dict]] = []

    def list_subscriptions(self, **kw):
        self.reads.append(("list_subscriptions", kw))
        return list(self.subscriptions.get(kw["customer"], []))

    def list_invoices(self, **kw):
        self.reads.append(("list_invoices", kw))
        return list(self.invoices.get(kw["customer"], []))

    def list_all_quotes(self, **kw):
        self.reads.append(("list_all_quotes", kw))
        return list(self.quotes.get(kw["customer"], []))


@pytest.fixture
def fake(monkeypatch) -> FakeStripe:
    f = FakeStripe()

    def forbidden(name):
        def call(*a, **kw):
            raise AssertionError(f"stripe_client.{name} called by a read-only pass")

        return call

    for name, fn in inspect.getmembers(stripe_client, inspect.isfunction):
        if fn.__module__ == stripe_client.__name__ and not name.startswith("_"):
            monkeypatch.setattr(stripe_client, name, forbidden(name))
    for name in READS:
        monkeypatch.setattr(stripe_client, name, getattr(f, name))
    return f


def company(
    customer: str | None = "cus_a", started: datetime | None = JUL_3, **kw
) -> cr.CompanyRow:
    t = Tenant(id=uuid.uuid4(), name=kw.pop("name", "Acme"), slug=uuid.uuid4().hex)
    t.plan = Plan.FREE
    t.stripe_customer_id = customer
    t.work_client_started_at = started
    t.stripe_subscriptions = kw.pop("subs", {})
    return cr.company_row(t, kw.pop("connected", None), now=NOW)


def one(fake: FakeStripe, row: cr.CompanyRow) -> cs.JoinedRow:
    (joined,) = cs.join([row], cs.read_all([row]))
    return joined


# ---------------------------------------------------------------------------
# What Stripe says about one customer
# ---------------------------------------------------------------------------


def test_free_client_only_has_day0_and_no_sale(fake) -> None:
    fake.subscriptions["cus_a"] = [
        sub(item("promptly_work_client", ts(2026, 7, 3), 500), start=ts(2026, 7, 3), pilot="true")
    ]
    facts = cs.read_customer("cus_a")
    assert facts.stripe_day0 == JUL_3
    assert facts.pilot_now is True
    assert facts.first_sale is None and facts.quoted_at is None and facts.paid_at is None
    assert facts.work_client_ended_at is None


def test_first_sale_is_the_earliest_module_item_across_subscriptions(fake) -> None:
    fake.subscriptions["cus_a"] = [
        sub(
            item("promptly_work_client", ts(2026, 7, 3)),
            item("promptly_learn_annual", ts(2026, 10, 1), 300),
            start=ts(2026, 7, 3),
            pilot="false",
        ),
        sub(item("promptly_suite_annual", ts(2026, 9, 14), 400), start=ts(2026, 9, 14)),
        # Atlas's Team plan and the platform fee are not module sales.
        sub(item("team_monthly", ts(2026, 8, 1)), start=ts(2026, 8, 1)),
        sub(item("promptly_platform_fee_annual", ts(2026, 8, 2)), start=ts(2026, 8, 2)),
    ]
    facts = cs.read_customer("cus_a")
    assert facts.first_sale == cs.Sale(
        datetime(2026, 9, 14, tzinfo=UTC), "promptly_suite_annual", 400
    )
    assert facts.first_suite_sold_at == datetime(2026, 9, 14, tzinfo=UTC)
    assert facts.pilot_now is False


def test_an_old_price_without_lookup_key_is_known_by_its_product(fake) -> None:
    fake.subscriptions["cus_a"] = [
        sub(item(None, ts(2026, 8, 5), 50, product="promptly_atlas"), start=ts(2026, 8, 5))
    ]
    assert cs.read_customer("cus_a").first_sale.module == "promptly_atlas_annual"


def test_a_removed_module_is_found_on_its_invoice(fake) -> None:
    fake.subscriptions["cus_a"] = [
        sub(item("promptly_work_client", ts(2026, 7, 3)), start=ts(2026, 7, 3))
    ]
    fake.invoices["cus_a"] = [
        # Old API shape: line.price, the product expanded.
        invoice(
            {"price": {"lookup_key": None, "product": {"id": "promptly_adopt"}}, "quantity": 80},
            created=ts(2026, 8, 20),
            paid=ts(2026, 9, 10),
        ),
        # 2025-03-31 and later: line.pricing.price_details.
        invoice(
            {"pricing": {"price_details": {"price": "price_x", "product": "promptly_learn"}}},
            created=ts(2026, 9, 1),
            status="open",
        ),
    ]
    facts = cs.read_customer("cus_a")
    assert facts.first_sale == cs.Sale(
        datetime(2026, 8, 20, tzinfo=UTC), "promptly_adopt_annual", 80
    )
    assert facts.paid_at == datetime(2026, 9, 10, tzinfo=UTC)


def test_paid_is_the_first_paid_module_invoice_only(fake) -> None:
    module_line = {"price": price("promptly_suite_annual"), "quantity": 400}
    fee_line = {"price": price("promptly_platform_fee_annual"), "quantity": 1}
    fake.invoices["cus_a"] = [
        invoice(fee_line, created=ts(2026, 8, 1), paid=ts(2026, 8, 2)),  # no module
        invoice(module_line, created=ts(2026, 8, 3), status="void"),  # never a sale
        invoice(module_line, created=ts(2026, 8, 4), status="draft"),
        invoice(module_line, created=ts(2026, 9, 1), status="open"),
        invoice(module_line, created=ts(2026, 9, 2), paid=ts(2026, 10, 1)),
    ]
    facts = cs.read_customer("cus_a")
    assert facts.paid_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert facts.first_sale.when == datetime(2026, 9, 1, tzinfo=UTC)


def test_quoted_is_the_first_finalised_module_quote(fake) -> None:
    fake.quotes["cus_a"] = [
        quote(None),  # a draft
        quote(ts(2026, 9, 2)),
        quote(ts(2026, 8, 1), kind="something_else"),
        quote(ts(2026, 8, 20)),
    ]
    assert cs.read_customer("cus_a").quoted_at == datetime(2026, 8, 20, tzinfo=UTC)


def test_work_client_ended_only_when_every_one_ended(fake) -> None:
    wc = item("promptly_work_client", ts(2026, 7, 3))
    fake.subscriptions["cus_a"] = [
        sub(wc, start=ts(2026, 7, 3), ended=ts(2026, 9, 1), id="sub_1"),
        sub(wc, start=ts(2026, 9, 5), id="sub_2"),
    ]
    facts = cs.read_customer("cus_a")
    assert facts.work_client_ended_at is None
    assert facts.stripe_day0 == JUL_3

    fake.subscriptions["cus_a"][1]["ended_at"] = ts(2026, 10, 1)
    assert cs.read_customer("cus_a").work_client_ended_at == datetime(2026, 10, 1, tzinfo=UTC)


def test_a_cut_short_list_is_named(fake) -> None:
    fake.subscriptions["cus_a"] = [sub(start=ts(2026, 7, 3), more=True, id="sub_big")]
    facts = cs.read_customer("cus_a")
    assert facts.warnings == ["sub_big: more than one page of items"]
    row = one(fake, company())
    assert row.as_csv()["stripe"] == "read, partial: sub_big: more than one page of items"


def test_reads_are_lists_by_customer_only(fake) -> None:
    cs.read_customer("cus_a")
    assert fake.reads == [
        ("list_subscriptions", {"customer": "cus_a", "status": "all"}),
        ("list_invoices", {"customer": "cus_a"}),
        ("list_all_quotes", {"customer": "cus_a"}),
    ]


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


def _sold(fake, customer: str, when: datetime, key: str = "promptly_suite_annual") -> None:
    fake.subscriptions[customer] = [
        sub(item("promptly_work_client", ts(2026, 7, 3)), start=ts(2026, 7, 3), pilot="true"),
        sub(item(key, int(when.timestamp()), 400), start=int(when.timestamp())),
    ]


def test_converted_within_six_months_of_day0(fake) -> None:
    _sold(fake, "cus_a", datetime(2026, 9, 14, tzinfo=UTC))
    row = one(fake, company())
    assert row.days_to_convert == 73
    assert row.converted_within_window is True
    assert row.existing_customer is False


def test_sale_after_six_calendar_months_is_late(fake) -> None:
    _sold(fake, "cus_a", datetime(2027, 1, 3, tzinfo=UTC))
    assert one(fake, company()).converted_within_window is False
    _sold(fake, "cus_a", datetime(2027, 1, 2, 23, tzinfo=UTC))
    assert one(fake, company()).converted_within_window is True


def test_sale_before_day0_is_an_existing_customer(fake) -> None:
    _sold(fake, "cus_a", datetime(2026, 3, 1, tzinfo=UTC), key="promptly_atlas_annual")
    row = one(fake, company())
    assert row.existing_customer is True
    assert row.days_to_convert is None
    assert row.converted_within_window is False


def test_no_sale_is_not_converted(fake) -> None:
    row = one(fake, company())
    assert row.converted_within_window is False
    assert row.existing_customer is None and row.days_to_convert is None


def test_stripe_day0_stands_in_when_atlas_has_none(fake) -> None:
    _sold(fake, "cus_a", datetime(2026, 8, 2, tzinfo=UTC))
    row = one(fake, company(started=None))
    assert row.day0 == JUL_3
    assert row.days_to_convert == 30


def test_company_without_customer_is_kept_and_not_read(fake) -> None:
    rows = cs.join(
        [company(customer=None, name="No Stripe")], cs.read_all([company(customer=None)])
    )
    assert fake.reads == []
    cells = rows[0].as_csv()
    assert cells["stripe"] == "no customer"
    assert cells["first_module_sold_at"] == "" and cells["converted_within_window"] == ""


def test_each_customer_is_read_once_and_rows_keep_the_report_order(fake) -> None:
    rows = [company("cus_b", name="B"), company("cus_a", name="A"), company("cus_b", name="B2")]
    joined = cs.join(rows, cs.read_all(rows))
    assert [r.company.name for r in joined] == ["B", "A", "B2"]
    assert [kw["customer"] for name, kw in fake.reads if name == "list_invoices"] == [
        "cus_b",
        "cus_a",
    ]


def test_csv_has_one_row_per_company_with_dates_and_flags(fake) -> None:
    _sold(fake, "cus_a", datetime(2026, 9, 14, tzinfo=UTC))
    fake.quotes["cus_a"] = [quote(ts(2026, 8, 20))]
    subs = {
        "sub_s": {"kind": "enterprise", "status": "active", "items": {"promptly_suite_annual": 400}}
    }
    row = company(name="Pilot Oy", subs=subs, connected=datetime(2026, 7, 10, tzinfo=UTC))
    out = cs.to_csv(cs.join([row], cs.read_all([row])))
    (record,) = list(csv.DictReader(io.StringIO(out)))
    assert list(record) == list(cs.COLUMNS)
    assert record["name"] == "Pilot Oy"
    assert record["cohort"] == "2026-07" and record["day0"] == "2026-07-03"
    assert record["pilot_now"] == "yes"
    assert record["guide_connected_at"] == "2026-07-10"
    assert record["quoted_at"] == "2026-08-20"
    assert record["first_module_sold_at"] == "2026-09-14"
    assert (record["first_module"], record["first_module_seats"]) == ("suite", "400")
    assert record["paid_at"] == ""
    assert record["days_to_convert"] == "73"
    assert record["converted_within_window"] == "yes"
    assert record["paid_module_now"] == "yes" and record["seats_now"] == "400"
    assert record["stripe"] == "read"


# ---------------------------------------------------------------------------
# What it reads
# ---------------------------------------------------------------------------


def test_calls_only_read_wrappers_and_reads_no_people() -> None:
    source = inspect.getsource(cs)
    tree = ast.parse(source)
    called = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "stripe_client"
    }
    assert called == set(READS)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported.isdisjoint({"app.models.user", "app.models.prompt_event", "stripe"})
    body = source.split('"""', 2)[2]
    for name in ("amount", "email", "GuideAdoption", "trial_events", "User"):
        assert name not in body, name


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------


def test_script_dry_run_plans_offline(monkeypatch, capsys) -> None:
    from scripts import stripe_conversion

    monkeypatch.setattr(stripe_conversion.get_settings(), "stripe_secret_key", "")

    async def no_db():
        raise AssertionError("database read on a dry run")

    monkeypatch.setattr(stripe_conversion, "load_companies", no_db)
    assert stripe_conversion.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Invoice.list" in out and "write    nothing" in out


def test_script_needs_a_key(monkeypatch) -> None:
    from scripts import stripe_conversion

    monkeypatch.setattr(stripe_conversion.get_settings(), "stripe_secret_key", "")
    assert stripe_conversion.main([]) == 2


@pytest.mark.parametrize("key", ["sk_live_x", "rk_live_x"])
def test_script_refuses_live_key_without_flag(monkeypatch, key) -> None:
    from scripts import stripe_conversion

    monkeypatch.setattr(stripe_conversion.get_settings(), "stripe_secret_key", key)
    assert stripe_conversion.main([]) == 2


def test_script_prints_csv(fake, monkeypatch, capsys) -> None:
    from scripts import stripe_conversion

    _sold(fake, "cus_a", datetime(2026, 9, 14, tzinfo=UTC))
    monkeypatch.setattr(stripe_conversion.get_settings(), "stripe_secret_key", "rk_live_x")

    async def rows():
        return [company(name="Pilot Oy"), company(customer=None, name="Free AB")]

    monkeypatch.setattr(stripe_conversion, "load_companies", rows)
    assert stripe_conversion.main(["--live"]) == 0
    captured = capsys.readouterr()
    records = list(csv.DictReader(io.StringIO(captured.out)))
    assert [r["name"] for r in records] == ["Pilot Oy", "Free AB"]
    assert records[0]["first_module_sold_at"] == "2026-09-14"
    assert "live mode: 2 companies, 1 customers read" in captured.err


@pytest.mark.asyncio
async def test_load_companies_reads_the_report_rows_and_writes_nothing(monkeypatch) -> None:
    import app.database as database
    from scripts import stripe_conversion

    async with TestSessionLocal() as s:
        s.add(
            Tenant(
                id=uuid.uuid4(),
                name="Pilot Oy",
                slug="conv-stripe-pilot",
                is_active=True,
                stripe_customer_id="cus_a",
                work_client_started_at=JUL_3,
            )
        )
        await s.commit()

    monkeypatch.setattr(database, "get_session_factory", lambda: TestSessionLocal)
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    rows = await stripe_conversion.load_companies()
    assert [(r.name, r.stripe_customer_id) for r in rows] == [("Pilot Oy", "cus_a")]


# ---------------------------------------------------------------------------
# On the real SDK's objects (no fake over the wrappers)
# ---------------------------------------------------------------------------


def test_read_customer_on_real_sdk_list_objects(monkeypatch) -> None:
    """The pass on what stripe-python returns, through the real wrappers.

    Since stripe-python 15 a StripeObject is not a dict (``.get()`` raises), so
    the dict fakes above cannot show the pass works on the SDK. Here the SDK's
    own ``list`` calls return its ``ListObject``s, built offline, and paging
    runs through ``auto_paging_iter``.
    """
    import stripe

    def page(*data):
        return stripe.ListObject.construct_from(
            {"object": "list", "data": list(data), "has_more": False, "url": "/v1/x"},
            "sk_test_offline",
        )

    subscriptions = page(
        sub(
            item("promptly_work_client", ts(2026, 7, 3)),
            item("promptly_learn_annual", ts(2026, 10, 1), 300),
            start=ts(2026, 7, 3),
            pilot="false",
        )
    )
    invoices = page(
        invoice(
            {"price": {"lookup_key": "promptly_adopt_annual", "product": "promptly_adopt"}},
            created=ts(2026, 8, 20),
            paid=ts(2026, 9, 10),
        )
    )
    listed_quotes = page(quote(ts(2026, 8, 1)))
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: subscriptions)
    monkeypatch.setattr(stripe.Invoice, "list", lambda **kw: invoices)
    monkeypatch.setattr(stripe.Quote, "list", lambda **kw: listed_quotes)

    assert all(type(s) is dict for s in stripe_client.list_subscriptions(customer="cus_a"))
    assert all(type(i) is dict for i in stripe_client.list_invoices(customer="cus_a"))
    assert all(type(q) is dict for q in stripe_client.list_all_quotes(customer="cus_a"))

    facts = cs.read_customer("cus_a")
    assert facts.stripe_day0 == JUL_3
    assert facts.pilot_now is False
    assert facts.first_sale == cs.Sale(
        datetime(2026, 8, 20, tzinfo=UTC), "promptly_adopt_annual", None
    )
    assert facts.paid_at == datetime(2026, 9, 10, tzinfo=UTC)
    assert facts.quoted_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert facts.warnings == []
