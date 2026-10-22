"""Free-to-paid conversion report (promptly-guide#43), from billing records only.

The definitions are promptly-guide's docs/conversion-metrics.md:

- a company is on the Work client if day 0 is recorded, Guide is connected or a
  module subscription carries the Work client;
- "paid now" means a live (active or trialing) subscription carries a paid
  module; past_due does not count, and the Team plan is not a module sale;
- cohorts are day 0's month; rates are withheld below ten companies;
- test tenants are left out, and nothing about people is read.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.guide_adoption import GuideConnection
from app.models.tenant import Plan, SubscriptionStatus, Tenant
from app.services import conversion_report as cr
from tests.conftest import TestSessionLocal, auth_header

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def _tenant(**kw) -> Tenant:
    t = Tenant(id=uuid.uuid4(), name=kw.pop("name", "Acme"), slug=uuid.uuid4().hex)
    t.stripe_subscriptions = {}
    t.plan = Plan.FREE
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _sub(status: str, **items) -> dict:
    return {"kind": "enterprise", "status": status, "items": items}


def _row(tenant: Tenant, connected: datetime | None = None) -> cr.CompanyRow:
    return cr.company_row(tenant, connected, now=NOW)


# ---------------------------------------------------------------------------
# One company
# ---------------------------------------------------------------------------


def test_free_work_client_only_is_started_not_paying():
    t = _tenant(
        work_client_started_at=datetime(2026, 7, 3, tzinfo=UTC),
        stripe_subscriptions={"sub_w": _sub("active", promptly_work_client=500)},
    )
    row = _row(t)
    assert row.cohort == "2026-07"
    assert row.work_client_live is True
    assert row.paid_module_now is False
    assert row.suite_now is False
    # The Work client's quantity is provisioned users, not a sale: not seats.
    assert row.seats_now is None
    assert row.lost is False


def test_suite_on_the_work_client_subscription_is_paid_and_suite():
    t = _tenant(
        work_client_started_at=datetime(2026, 7, 3, tzinfo=UTC),
        stripe_subscriptions={
            "sub_w": _sub("active", promptly_work_client=500, promptly_suite_annual=400)
        },
    )
    row = _row(t)
    assert row.paid_module_now is True
    assert row.suite_now is True
    assert row.seats_now == 400


def test_seats_are_counted_once_across_modules():
    t = _tenant(
        stripe_subscriptions={
            "sub_w": _sub("active", promptly_work_client=500),
            "sub_q": _sub("active", promptly_atlas_annual=300, promptly_learn_annual=200),
        },
    )
    row = _row(t)
    assert row.paid_module_now is True
    assert row.suite_now is False
    assert row.seats_now == 300


def test_past_due_module_is_not_paid_now():
    t = _tenant(stripe_subscriptions={"sub_q": _sub("past_due", promptly_atlas_annual=50)})
    assert _row(t).paid_module_now is False


def test_team_plan_is_not_a_module_sale():
    t = _tenant(
        plan=Plan.TEAM,
        subscription_status=SubscriptionStatus.ACTIVE,
        work_client_started_at=datetime(2026, 7, 3, tzinfo=UTC),
    )
    assert _row(t).paid_module_now is False


def test_lost_when_the_work_client_ended_with_no_module_on_record():
    t = _tenant(stripe_subscriptions={"sub_w": _sub("canceled", promptly_work_client=10)})
    assert _row(t).lost is True


def test_not_lost_when_a_module_was_on_record():
    t = _tenant(
        stripe_subscriptions={
            "sub_w": _sub("canceled", promptly_work_client=10),
            "sub_q": _sub("canceled", promptly_learn_annual=10),
        }
    )
    row = _row(t)
    assert row.lost is False
    assert row.paid_module_now is False


def test_window_is_six_calendar_months_from_day_zero():
    open_ = _tenant(work_client_started_at=datetime(2026, 4, 23, tzinfo=UTC))
    closed = _tenant(work_client_started_at=datetime(2026, 4, 22, tzinfo=UTC))
    assert _row(open_).window_open is True
    assert _row(closed).window_open is False


def test_add_months_clamps_to_the_month_end():
    assert cr.add_months(datetime(2026, 8, 31, tzinfo=UTC), 6) == datetime(2027, 2, 28, tzinfo=UTC)
    assert cr.add_months(datetime(2026, 1, 15, tzinfo=UTC), 6) == datetime(2026, 7, 15, tzinfo=UTC)


def test_free_client_status_comes_from_the_ninety_day_rule():
    t = _tenant(work_client_started_at=NOW - timedelta(days=120))
    assert _row(t).free_client_status == "endpoint_required"


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------


def _company(cohort: str | None, *, paid=False, suite=False, seats=None, window_open=True):
    return cr.CompanyRow(
        tenant_id=uuid.uuid4(),
        name="c",
        stripe_customer_id=None,
        cohort=cohort,
        work_client_started_at=None,
        guide_connected_at=None,
        work_client_live=True,
        paid_module_now=paid,
        suite_now=suite,
        seats_now=seats,
        lost=False,
        free_client_status=None,
        window_open=window_open,
    )


def test_small_cohort_shows_counts_and_no_rate():
    rows = [_company("2026-07", paid=True, suite=True, seats=400)] + [
        _company("2026-07") for _ in range(2)
    ]
    (cohort,) = cr.cohort_rows(rows)
    assert (cohort.started, cohort.paying_now_any_module, cohort.paying_now_suite) == (3, 1, 1)
    assert cohort.paying_now_rate_any_module is None
    assert cohort.paying_now_rate_suite is None
    assert cohort.median_seats_paying == 400
    assert cohort.window == "so far"


def test_cohort_of_ten_gets_a_rate_any_module_and_suite_apart():
    rows = (
        [_company("2026-01", paid=True, suite=True, window_open=False) for _ in range(2)]
        + [_company("2026-01", paid=True, window_open=False) for _ in range(3)]
        + [_company("2026-01", window_open=False) for _ in range(5)]
    )
    (cohort,) = cr.cohort_rows(rows)
    assert cohort.paying_now_rate_any_module == 0.5
    assert cohort.paying_now_rate_suite == 0.2
    assert cohort.window == "closed"


def test_cohorts_in_month_order_with_unknown_day_zero_last():
    rows = [_company(None), _company("2026-08"), _company("2026-07")]
    assert [c.cohort for c in cr.cohort_rows(rows)] == ["2026-07", "2026-08", None]


# ---------------------------------------------------------------------------
# What it reads
# ---------------------------------------------------------------------------


def test_reads_no_people_and_no_usage():
    """The note's "never collected, and never used" list, checked on the source."""
    source = inspect.getsource(cr)
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported.isdisjoint(
        {"app.models.user", "app.models.prompt_event", "app.models.llm_usage"}
    )
    for name in ("GuideAdoption", "trial_events", "TrialEvent", "User"):
        assert name not in source.split('"""', 2)[2], name


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


async def _seed() -> dict[str, uuid.UUID]:
    ids = {}
    async with TestSessionLocal() as s:

        def add(key: str, **kw) -> Tenant:
            t = Tenant(id=uuid.uuid4(), name=key, slug=f"conv-{key}", is_active=True, **kw)
            s.add(t)
            ids[key] = t.id
            return t

        add(
            "pilot",
            work_client_started_at=datetime(2026, 7, 3, tzinfo=UTC),
            stripe_customer_id="cus_pilot",
            stripe_subscriptions={
                "sub_w": _sub("active", promptly_work_client=500, promptly_suite_annual=400)
            },
        )
        add("free", work_client_started_at=datetime(2026, 7, 20, tzinfo=UTC))
        add("connected_only")
        add("atlas_only", plan=Plan.TEAM)  # Atlas Team customer, no Work client
        add(
            "test_tenant",
            is_test_data=True,
            work_client_started_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        await s.flush()
        s.add(
            GuideConnection(
                tenant_id=ids["connected_only"], firebase_project_id="p1", offered_kinds=[]
            )
        )
        s.add(GuideConnection(tenant_id=ids["pilot"], firebase_project_id="p2", offered_kinds=[]))
        await s.commit()
    return ids


@pytest.mark.asyncio
async def test_endpoint_reports_work_client_companies_only(client, super_admin_token):
    ids = await _seed()
    resp = await client.get("/api/v1/admin/conversion", headers=auth_header(super_admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    names = [c["name"] for c in body["companies"]]
    assert names == ["pilot", "free", "connected_only"]

    pilot = body["companies"][0]
    assert pilot["tenant_id"] == str(ids["pilot"])
    assert pilot["paid_module_now"] is True and pilot["suite_now"] is True
    assert pilot["seats_now"] == 400
    assert pilot["guide_connected_at"] is not None

    july, unknown = body["cohorts"]
    assert july["cohort"] == "2026-07"
    assert (july["started"], july["connected"], july["paying_now_any_module"]) == (2, 1, 1)
    assert july["paying_now_rate_any_module"] is None
    assert unknown["cohort"] is None and unknown["started"] == 1

    assert body["min_companies_for_rate"] == 10
    assert set(body["not_from_atlas"]) >= {"converted_at", "time_to_convert", "pilot"}


@pytest.mark.asyncio
async def test_endpoint_is_super_admin_only(client, tenant_admin_token):
    resp = await client.get("/api/v1/admin/conversion", headers=auth_header(tenant_admin_token))
    assert resp.status_code == 403
