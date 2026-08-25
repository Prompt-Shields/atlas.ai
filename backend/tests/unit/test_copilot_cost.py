"""Unit tests for the GitHub Copilot cost connector's pure normalisation logic.

Only ``_normalize`` is exercised here — it holds all the mapping logic and
takes no network. The live HTTP path in ``fetch_cost`` is intentionally not
covered (see the module docstring in ``copilot_cost.py``).

Copilot bills per *seat*, not metered tokens. The billing endpoint reports the
current seat count (a snapshot); the metrics endpoint reports per-day engaged
users. We derive cost = seats x price, where price comes from connector config
(``copilot_seat_price_usd``) or a documented module-level DEFAULT. Docs:
  https://docs.github.com/en/rest/copilot/copilot-user-management   (billing)
  https://docs.github.com/en/rest/copilot/copilot-metrics           (metrics)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ai_cost_record import CostKind, CostProvider, CostSource, CostSubjectKind
from app.services.cost.copilot_cost import (
    DEFAULT_SEAT_PRICE_USD,
    CopilotCostConnector,
    _normalize,
)

TODAY = date(2025, 8, 2)


# GET /orgs/{org}/copilot/billing — point-in-time seat snapshot.
BILLING_PAYLOAD = {
    "seat_breakdown": {
        "total": 10,
        "added_this_cycle": 2,
        "pending_cancellation": 0,
        "pending_invitation": 1,
        "active_this_cycle": 8,
        "inactive_this_cycle": 2,
    },
    "seat_management_setting": "assign_selected",
    "public_code_suggestions": "block",
    "plan_type": "business",
}


# GET /orgs/{org}/copilot/metrics — one row per day.
METRICS_PAYLOAD = [
    {"date": "2025-08-01", "total_active_users": 7, "total_engaged_users": 5},
    {"date": "2025-08-02", "total_active_users": 9, "total_engaged_users": 6},
]


def test_normalize_one_seat_record_per_metrics_day():
    records = _normalize(BILLING_PAYLOAD, METRICS_PAYLOAD, Decimal("19.00"), TODAY)
    assert len(records) == 2


def test_normalize_seat_subscription_tags_and_derived_cost():
    records = _normalize(BILLING_PAYLOAD, METRICS_PAYLOAD, Decimal("19.00"), TODAY)
    by_day = {r["usage_date"]: r for r in records}

    day1 = by_day[date(2025, 8, 1)]
    assert day1["cost_kind"] is CostKind.seat_subscription
    assert day1["subject_kind"] is CostSubjectKind.sku
    assert day1["cost_source"] is CostSource.derived_seats
    assert day1["seats"] == 10
    # 10 seats x $19.00 = $190.00 — never 0 when seats > 0.
    assert day1["cost_usd"] == Decimal("190.00")
    assert isinstance(day1["cost_usd"], Decimal)
    assert day1["tokens_in"] is None
    assert day1["tokens_out"] is None


def test_normalize_engaged_users_become_quantity():
    records = _normalize(BILLING_PAYLOAD, METRICS_PAYLOAD, Decimal("19.00"), TODAY)
    by_day = {r["usage_date"]: r for r in records}
    assert by_day[date(2025, 8, 1)]["quantity"] == Decimal("5")
    assert by_day[date(2025, 8, 2)]["quantity"] == Decimal("6")


def test_normalize_provisional_only_for_today():
    records = _normalize(BILLING_PAYLOAD, METRICS_PAYLOAD, Decimal("19.00"), TODAY)
    by_day = {r["usage_date"]: r for r in records}
    assert by_day[date(2025, 8, 1)]["is_provisional"] is False
    assert by_day[date(2025, 8, 2)]["is_provisional"] is True


def test_normalize_uses_configured_price():
    # (a) configured price path: seats x configured price.
    records = _normalize(BILLING_PAYLOAD, METRICS_PAYLOAD, Decimal("39.00"), TODAY)
    by_day = {r["usage_date"]: r for r in records}
    # 10 seats x $39.00 = $390.00.
    assert by_day[date(2025, 8, 1)]["cost_usd"] == Decimal("390.00")
    assert by_day[date(2025, 8, 1)]["cost_source"] is CostSource.derived_seats


def test_normalize_default_price_fallback():
    # (b) default-fallback path: seats x DEFAULT_SEAT_PRICE_USD.
    records = _normalize(BILLING_PAYLOAD, METRICS_PAYLOAD, DEFAULT_SEAT_PRICE_USD, TODAY)
    by_day = {r["usage_date"]: r for r in records}
    assert by_day[date(2025, 8, 1)]["cost_usd"] == Decimal("10") * DEFAULT_SEAT_PRICE_USD
    assert by_day[date(2025, 8, 1)]["cost_source"] is CostSource.derived_seats


def test_normalize_never_zero_cost_when_seats_positive():
    records = _normalize(BILLING_PAYLOAD, METRICS_PAYLOAD, DEFAULT_SEAT_PRICE_USD, TODAY)
    for r in records:
        assert r["seats"] == 10
        assert r["cost_usd"] > Decimal("0")


def test_normalize_no_metrics_falls_back_to_single_today_record():
    # When the metrics endpoint returns nothing, still emit a seat record so
    # the seat subscription is never silently dropped — dated today.
    records = _normalize(BILLING_PAYLOAD, [], Decimal("19.00"), TODAY)
    assert len(records) == 1
    assert records[0]["usage_date"] == TODAY
    assert records[0]["seats"] == 10
    assert records[0]["cost_usd"] == Decimal("190.00")
    assert records[0]["quantity"] is None
    assert records[0]["is_provisional"] is True


def test_normalize_zero_seats_emits_zero_cost():
    billing = {"seat_breakdown": {"total": 0}}
    records = _normalize(billing, METRICS_PAYLOAD, Decimal("19.00"), TODAY)
    for r in records:
        assert r["seats"] == 0
        assert r["cost_usd"] == Decimal("0")


def test_connector_provider_and_protocol():
    from app.services.cost.base import CostConnector

    connector = CopilotCostConnector()
    assert connector.provider is CostProvider.github_copilot
    assert isinstance(connector, CostConnector)


def test_connector_is_registered():
    from app.services.cost import registry

    assert CostProvider.github_copilot in registry.registered_providers()
    assert isinstance(registry.get_connector(CostProvider.github_copilot), CopilotCostConnector)
