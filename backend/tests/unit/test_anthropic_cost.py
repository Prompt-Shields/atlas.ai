"""Unit tests for the Anthropic cost connector's pure normalisation logic.

Only ``_normalize`` is exercised here — it contains all the mapping logic and
takes no network. The live HTTP path in ``fetch_cost`` is intentionally not
covered (see the module docstring in ``anthropic_cost.py``).

Fixtures below mirror the real documented response shapes of:
  GET /v1/organizations/cost_report           (group_by=description)
  GET /v1/organizations/usage_report/messages (group_by=model)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ai_cost_record import CostKind, CostSource, CostSubjectKind
from app.services.cost.anthropic_cost import AnthropicCostConnector, _normalize

# Two days, one model. ``today`` is the 2nd day so it should be provisional.
DAY1 = "2025-08-01T00:00:00Z"  # starting_at, inclusive
DAY1_END = "2025-08-02T00:00:00Z"
DAY2 = "2025-08-02T00:00:00Z"
DAY2_END = "2025-08-03T00:00:00Z"
TODAY = date(2025, 8, 2)
MODEL = "claude-opus-4-6"


# Cost Report grouped by description: amount is a decimal string in *cents*.
# Each (day, model) is split across multiple token_type rows that must be summed.
COST_PAYLOAD = {
    "data": [
        {
            "starting_at": DAY1,
            "ending_at": DAY1_END,
            "results": [
                {
                    "amount": "100.00",  # $1.00 — input tokens
                    "currency": "USD",
                    "cost_type": "tokens",
                    "token_type": "uncached_input_tokens",
                    "description": "Claude Opus Usage - Input Tokens",
                    "model": MODEL,
                },
                {
                    "amount": "50.00",  # $0.50 — output tokens
                    "currency": "USD",
                    "cost_type": "tokens",
                    "token_type": "output_tokens",
                    "description": "Claude Opus Usage - Output Tokens",
                    "model": MODEL,
                },
            ],
        },
        {
            "starting_at": DAY2,
            "ending_at": DAY2_END,
            "results": [
                {
                    "amount": "250.00",  # $2.50
                    "currency": "USD",
                    "cost_type": "tokens",
                    "token_type": "uncached_input_tokens",
                    "description": "Claude Opus Usage - Input Tokens",
                    "model": MODEL,
                },
            ],
        },
    ],
    "has_more": False,
    "next_page": None,
}


# Usage Report grouped by model: token counts per (day, model).
USAGE_PAYLOAD = {
    "data": [
        {
            "starting_at": DAY1,
            "ending_at": DAY1_END,
            "results": [
                {
                    "model": MODEL,
                    "uncached_input_tokens": 1000,
                    "cache_read_input_tokens": 200,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 100,
                        "ephemeral_5m_input_tokens": 50,
                    },
                    "output_tokens": 500,
                },
            ],
        },
        {
            "starting_at": DAY2,
            "ending_at": DAY2_END,
            "results": [
                {
                    "model": MODEL,
                    "uncached_input_tokens": 3000,
                    "cache_read_input_tokens": 0,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 0,
                        "ephemeral_5m_input_tokens": 0,
                    },
                    "output_tokens": 1500,
                },
            ],
        },
    ],
    "has_more": False,
    "next_page": None,
}


def test_normalize_record_count_one_per_day_model():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    assert len(records) == 2  # one per (day, model)


def test_normalize_tags_and_decimal_cost():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    by_day = {r["usage_date"]: r for r in records}

    day1 = by_day[date(2025, 8, 1)]
    assert day1["cost_kind"] is CostKind.metered_usage
    assert day1["subject_kind"] is CostSubjectKind.model
    assert day1["subject_ref"] == MODEL
    assert day1["cost_source"] is CostSource.vendor_reported
    # 100.00 + 50.00 cents = 150 cents = $1.50
    assert day1["cost_usd"] == Decimal("1.50")
    assert isinstance(day1["cost_usd"], Decimal)
    assert day1["seats"] is None
    assert day1["quantity"] is None

    day2 = by_day[date(2025, 8, 2)]
    assert day2["cost_usd"] == Decimal("2.50")


def test_normalize_merges_tokens_onto_right_day_model():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    by_day = {r["usage_date"]: r for r in records}

    day1 = by_day[date(2025, 8, 1)]
    # tokens_in = uncached + cache_read + cache_creation(1h+5m) = 1000+200+100+50
    assert day1["tokens_in"] == 1350
    assert day1["tokens_out"] == 500

    day2 = by_day[date(2025, 8, 2)]
    assert day2["tokens_in"] == 3000
    assert day2["tokens_out"] == 1500


def test_normalize_provisional_only_for_today():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    by_day = {r["usage_date"]: r for r in records}

    assert by_day[date(2025, 8, 1)]["is_provisional"] is False
    assert by_day[date(2025, 8, 2)]["is_provisional"] is True  # today's bucket


def test_normalize_handles_missing_usage_rows():
    # Cost with no matching usage row → tokens stay None, cost still emitted.
    records = _normalize(COST_PAYLOAD, {"data": [], "has_more": False}, TODAY)
    assert len(records) == 2
    for r in records:
        assert r["tokens_in"] is None
        assert r["tokens_out"] is None


def test_connector_provider_and_protocol():
    from app.models.ai_cost_record import CostProvider
    from app.services.cost.base import CostConnector

    connector = AnthropicCostConnector()
    assert connector.provider is CostProvider.anthropic
    assert isinstance(connector, CostConnector)


def test_connector_is_registered():
    from app.models.ai_cost_record import CostProvider
    from app.services.cost import registry

    # Importing the module registers the connector at import time.
    assert CostProvider.anthropic in registry.registered_providers()
    assert isinstance(registry.get_connector(CostProvider.anthropic), AnthropicCostConnector)
