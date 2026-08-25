"""Unit tests for the Vercel cost connector's pure normalisation logic.

Only ``_normalize`` is exercised here — it holds all the mapping logic and
takes no network. The live HTTP path in ``fetch_cost`` is intentionally not
covered (see the module docstring in ``vercel_cost.py``).

Two sources are normalised:
  * FOCUS v1.3 billing charges (GET https://api.vercel.com/v1/billing/charges)
    → infra spend, vendor-reported, BilledCost in USD per ChargePeriodStart day.
  * AI Gateway custom report (GET https://ai-gateway.vercel.sh/v1/report,
    group_by=model, date_part=day) → metered_usage with total_cost USD +
    input/output tokens, only when the tenant opts in via config.
Docs:
  https://vercel.com/docs/rest-api/billing/list-focus-billing-charges
  https://vercel.com/docs/ai-gateway/capabilities/custom-reporting
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ai_cost_record import CostKind, CostProvider, CostSource, CostSubjectKind
from app.services.cost.vercel_cost import VercelCostConnector, _normalize

TODAY = date(2025, 8, 2)


# FOCUS v1.3 charge rows (already parsed from JSONL into a list of dicts).
# BilledCost is the USD invoicing amount; ChargePeriodStart is the day.
FOCUS_CHARGES = [
    {
        "BilledCost": 4.20,
        "EffectiveCost": 4.20,
        "BillingCurrency": "USD",
        "ChargeCategory": "Usage",
        "ChargePeriodStart": "2025-08-01T00:00:00Z",
        "ChargePeriodEnd": "2025-08-02T00:00:00Z",
        "ServiceName": "Edge Functions",
        "ConsumedQuantity": 1000,
        "ConsumedUnit": "invocations",
    },
    {
        "BilledCost": 1.10,
        "EffectiveCost": 1.10,
        "BillingCurrency": "USD",
        "ChargeCategory": "Usage",
        "ChargePeriodStart": "2025-08-01T00:00:00Z",
        "ChargePeriodEnd": "2025-08-02T00:00:00Z",
        "ServiceName": "Bandwidth",
        "ConsumedQuantity": 5,
        "ConsumedUnit": "GB",
    },
    {
        "BilledCost": 6.30,
        "EffectiveCost": 6.30,
        "BillingCurrency": "USD",
        "ChargeCategory": "Usage",
        "ChargePeriodStart": "2025-08-02T00:00:00Z",
        "ChargePeriodEnd": "2025-08-03T00:00:00Z",
        "ServiceName": "Edge Functions",
        "ConsumedQuantity": 1500,
        "ConsumedUnit": "invocations",
    },
]


# AI Gateway report rows (group_by=model, date_part=day): total_cost in USD.
GATEWAY_REPORT = {
    "results": [
        {
            "day": "2025-08-01",
            "model": "anthropic/claude-sonnet-4.6",
            "total_cost": 10.5,
            "input_tokens": 1000,
            "output_tokens": 500,
            "request_count": 25,
        },
        {
            "day": "2025-08-02",
            "model": "openai/gpt-4o",
            "total_cost": 3.25,
            "input_tokens": 400,
            "output_tokens": 200,
            "request_count": 8,
        },
    ]
}


def test_normalize_billing_only_one_record_per_day_service():
    records = _normalize(FOCUS_CHARGES, None, TODAY)
    # day1: Edge Functions + Bandwidth; day2: Edge Functions → 3 records.
    assert len(records) == 3
    for r in records:
        assert r["cost_kind"] is CostKind.infra
        assert r["cost_source"] is CostSource.vendor_reported
        assert r["subject_kind"] is CostSubjectKind.sku


def test_normalize_billing_tags_and_usd_cost():
    records = _normalize(FOCUS_CHARGES, None, TODAY)
    by_key = {(r["usage_date"], r["subject_ref"]): r for r in records}

    edge1 = by_key[(date(2025, 8, 1), "Edge Functions")]
    assert edge1["cost_usd"] == Decimal("4.20")
    assert isinstance(edge1["cost_usd"], Decimal)
    assert edge1["seats"] is None
    assert edge1["tokens_in"] is None
    assert edge1["quantity"] == Decimal("1000")

    band1 = by_key[(date(2025, 8, 1), "Bandwidth")]
    assert band1["cost_usd"] == Decimal("1.10")

    edge2 = by_key[(date(2025, 8, 2), "Edge Functions")]
    assert edge2["cost_usd"] == Decimal("6.30")


def test_normalize_billing_sums_same_day_service():
    charges = [
        *FOCUS_CHARGES,
        {
            "BilledCost": 0.80,
            "BillingCurrency": "USD",
            "ChargeCategory": "Usage",
            "ChargePeriodStart": "2025-08-01T00:00:00Z",
            "ServiceName": "Edge Functions",
        },
    ]
    records = _normalize(charges, None, TODAY)
    by_key = {(r["usage_date"], r["subject_ref"]): r for r in records}
    # 4.20 + 0.80 summed onto the same (day, service).
    assert by_key[(date(2025, 8, 1), "Edge Functions")]["cost_usd"] == Decimal("5.00")


def test_normalize_billing_provisional_only_for_today():
    records = _normalize(FOCUS_CHARGES, None, TODAY)
    for r in records:
        if r["usage_date"] == date(2025, 8, 2):
            assert r["is_provisional"] is True
        else:
            assert r["is_provisional"] is False


def test_normalize_gateway_disabled_emits_no_metered_rows():
    # When the gateway payload is None (flag off), only infra rows appear.
    records = _normalize(FOCUS_CHARGES, None, TODAY)
    assert all(r["cost_kind"] is CostKind.infra for r in records)


def test_normalize_gateway_rows_are_metered_usage_with_tokens():
    records = _normalize([], GATEWAY_REPORT, TODAY)
    assert len(records) == 2
    by_key = {(r["usage_date"], r["subject_ref"]): r for r in records}

    sonnet = by_key[(date(2025, 8, 1), "anthropic/claude-sonnet-4.6")]
    assert sonnet["cost_kind"] is CostKind.metered_usage
    assert sonnet["cost_source"] is CostSource.vendor_reported
    assert sonnet["subject_kind"] is CostSubjectKind.other
    assert sonnet["cost_usd"] == Decimal("10.5")
    assert sonnet["tokens_in"] == 1000
    assert sonnet["tokens_out"] == 500
    assert sonnet["is_provisional"] is False

    gpt = by_key[(date(2025, 8, 2), "openai/gpt-4o")]
    assert gpt["cost_usd"] == Decimal("3.25")
    assert gpt["is_provisional"] is True


def test_normalize_combines_billing_and_gateway():
    records = _normalize(FOCUS_CHARGES, GATEWAY_REPORT, TODAY)
    kinds = {r["cost_kind"] for r in records}
    assert kinds == {CostKind.infra, CostKind.metered_usage}
    assert len(records) == 5  # 3 infra + 2 metered


def test_normalize_handles_empty_inputs():
    assert _normalize([], None, TODAY) == []
    assert _normalize([], {"results": []}, TODAY) == []


def test_connector_provider_and_protocol():
    from app.services.cost.base import CostConnector

    connector = VercelCostConnector()
    assert connector.provider is CostProvider.vercel
    assert isinstance(connector, CostConnector)


def test_connector_is_registered():
    from app.services.cost import registry

    assert CostProvider.vercel in registry.registered_providers()
    assert isinstance(registry.get_connector(CostProvider.vercel), VercelCostConnector)
