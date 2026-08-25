"""Unit tests for the OpenAI cost connector's pure normalisation logic.

Only ``_normalize`` is exercised here — it holds all the mapping logic and
takes no network. The live HTTP path in ``fetch_cost`` is intentionally not
covered (see the module docstring in ``openai_cost.py``).

Fixtures below mirror the real documented response shapes of:
  GET /v1/organization/costs                (group_by=line_item)
  GET /v1/organization/usage/completions    (group_by=model)

Both use Unix-second ``start_time``/``end_time`` bucket boundaries and an
``amount.value`` reported in *dollars* (not cents). Docs:
  https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs
  https://developers.openai.com/api/reference/resources/organization/subresources/usage/methods/get_completions
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.ai_cost_record import CostKind, CostProvider, CostSource, CostSubjectKind
from app.services.cost.openai_cost import OpenAICostConnector, _normalize


def _unix(d: str) -> int:
    """Midnight-UTC Unix seconds for an ISO date string."""
    return int(datetime.fromisoformat(d).replace(tzinfo=UTC).timestamp())


# Two days. ``today`` is the 2nd day so its bucket is provisional.
DAY1_START = _unix("2025-08-01")
DAY1_END = _unix("2025-08-02")
DAY2_START = _unix("2025-08-02")
DAY2_END = _unix("2025-08-03")
TODAY = date(2025, 8, 2)
MODEL = "gpt-4o-2024-08-06"


# Costs API grouped by line_item: amount.value is a number in *dollars*.
# A single (day, line_item) can appear across rows that we sum.
COST_PAYLOAD = {
    "object": "page",
    "data": [
        {
            "object": "bucket",
            "start_time": DAY1_START,
            "end_time": DAY1_END,
            "results": [
                {
                    "object": "organization.costs.result",
                    "amount": {"value": 1.50, "currency": "usd"},
                    "line_item": f"{MODEL}, input",
                    "project_id": None,
                },
                {
                    "object": "organization.costs.result",
                    "amount": {"value": 0.50, "currency": "usd"},
                    "line_item": f"{MODEL}, output",
                    "project_id": None,
                },
            ],
        },
        {
            "object": "bucket",
            "start_time": DAY2_START,
            "end_time": DAY2_END,
            "results": [
                {
                    "object": "organization.costs.result",
                    "amount": {"value": 2.50, "currency": "usd"},
                    "line_item": f"{MODEL}, input",
                    "project_id": None,
                },
            ],
        },
    ],
    "has_more": False,
    "next_page": None,
}


# Usage API grouped by model: token counts per (day, model).
USAGE_PAYLOAD = {
    "object": "page",
    "data": [
        {
            "object": "bucket",
            "start_time": DAY1_START,
            "end_time": DAY1_END,
            "results": [
                {
                    "object": "organization.usage.completions.result",
                    "input_tokens": 1000,
                    "input_cached_tokens": 200,
                    "output_tokens": 500,
                    "num_model_requests": 3,
                    "model": MODEL,
                },
            ],
        },
        {
            "object": "bucket",
            "start_time": DAY2_START,
            "end_time": DAY2_END,
            "results": [
                {
                    "object": "organization.usage.completions.result",
                    "input_tokens": 3000,
                    "input_cached_tokens": 0,
                    "output_tokens": 1500,
                    "num_model_requests": 9,
                    "model": MODEL,
                },
            ],
        },
    ],
    "has_more": False,
    "next_page": None,
}


def test_normalize_record_count_one_per_day_lineitem():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    # day1 has two distinct line items, day2 has one → 3 records.
    assert len(records) == 3


def test_normalize_tags_and_decimal_cost_in_dollars():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    by_key = {(r["usage_date"], r["subject_ref"]): r for r in records}

    day1_in = by_key[(date(2025, 8, 1), f"{MODEL}, input")]
    assert day1_in["cost_kind"] is CostKind.metered_usage
    assert day1_in["subject_kind"] is CostSubjectKind.model
    assert day1_in["cost_source"] is CostSource.vendor_reported
    # amount.value is already dollars — 1.50 stays 1.50 (no cents conversion).
    assert day1_in["cost_usd"] == Decimal("1.50")
    assert isinstance(day1_in["cost_usd"], Decimal)
    assert day1_in["seats"] is None
    assert day1_in["quantity"] is None

    day1_out = by_key[(date(2025, 8, 1), f"{MODEL}, output")]
    assert day1_out["cost_usd"] == Decimal("0.50")

    day2_in = by_key[(date(2025, 8, 2), f"{MODEL}, input")]
    assert day2_in["cost_usd"] == Decimal("2.50")


def test_normalize_merges_tokens_onto_matching_model_lineitem():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    by_key = {(r["usage_date"], r["subject_ref"]): r for r in records}

    # Both day-1 line items mention the model, so each receives the day's
    # model token totals (input incl. cached = 1000, output = 500).
    day1_in = by_key[(date(2025, 8, 1), f"{MODEL}, input")]
    assert day1_in["tokens_in"] == 1000
    assert day1_in["tokens_out"] == 500

    day2_in = by_key[(date(2025, 8, 2), f"{MODEL}, input")]
    assert day2_in["tokens_in"] == 3000
    assert day2_in["tokens_out"] == 1500


def test_normalize_provisional_only_for_today():
    records = _normalize(COST_PAYLOAD, USAGE_PAYLOAD, TODAY)
    for r in records:
        if r["usage_date"] == date(2025, 8, 2):
            assert r["is_provisional"] is True
        else:
            assert r["is_provisional"] is False


def test_normalize_handles_missing_usage_rows():
    records = _normalize(COST_PAYLOAD, {"data": [], "has_more": False}, TODAY)
    assert len(records) == 3
    for r in records:
        assert r["tokens_in"] is None
        assert r["tokens_out"] is None


def test_normalize_line_item_without_model_match_has_no_tokens():
    cost = {
        "data": [
            {
                "start_time": DAY1_START,
                "end_time": DAY1_END,
                "results": [
                    {
                        "amount": {"value": 0.06, "currency": "usd"},
                        "line_item": "web search tool calls",
                        "project_id": None,
                    },
                ],
            }
        ],
        "has_more": False,
    }
    records = _normalize(cost, USAGE_PAYLOAD, TODAY)
    assert len(records) == 1
    assert records[0]["subject_ref"] == "web search tool calls"
    assert records[0]["tokens_in"] is None
    assert records[0]["cost_usd"] == Decimal("0.06")


def test_connector_provider_and_protocol():
    from app.services.cost.base import CostConnector

    connector = OpenAICostConnector()
    assert connector.provider is CostProvider.openai
    assert isinstance(connector, CostConnector)


def test_connector_is_registered():
    from app.services.cost import registry

    assert CostProvider.openai in registry.registered_providers()
    assert isinstance(registry.get_connector(CostProvider.openai), OpenAICostConnector)
