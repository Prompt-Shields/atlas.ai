"""Unit tests for the Cursor cost connector's pure normalisation logic.

Only ``_normalize`` is exercised here — it holds all the mapping logic and
takes no network. The live HTTP path in ``fetch_cost`` is intentionally not
covered (see the module docstring in ``cursor_cost.py``).

Fixtures below mirror the real documented response shape of the Cursor Teams
Admin API:
  POST https://api.cursor.com/teams/spend   (Basic auth, API key as username)

The spend endpoint reports *current billing cycle* per-member spend — there is
no per-day cost grain — so every record is dated to ``today`` and flagged
provisional. ``spendCents`` is on-demand spend in **cents**. Docs:
  https://cursor.com/docs/account/teams/admin-api
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ai_cost_record import CostKind, CostProvider, CostSource, CostSubjectKind
from app.services.cost.cursor_cost import CursorCostConnector, _normalize

TODAY = date(2025, 8, 2)


# /teams/spend — one page, two members. ``spendCents`` is on-demand spend in
# cents for the current billing cycle.
SPEND_PAYLOAD = {
    "teamMemberSpend": [
        {
            "userId": 1,
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "role": "owner",
            "spendCents": 1250,  # $12.50
            "overallSpendCents": 4250,
            "fastPremiumRequests": 320,
            "hardLimitOverrideDollars": 0,
        },
        {
            "userId": 2,
            "name": "Alan Turing",
            "email": "alan@example.com",
            "role": "member",
            "spendCents": 0,  # included usage only
            "overallSpendCents": 1900,
            "fastPremiumRequests": 95,
            "hardLimitOverrideDollars": 0,
        },
    ],
    "subscriptionCycleStart": 1722470400000,
    "totalMembers": 2,
    "totalPages": 1,
}


def test_normalize_one_record_per_member():
    records = _normalize(SPEND_PAYLOAD, TODAY)
    assert len(records) == 2


def test_normalize_tags_and_decimal_cost_in_dollars():
    records = _normalize(SPEND_PAYLOAD, TODAY)
    by_member = {r["subject_ref"]: r for r in records}

    ada = by_member["ada@example.com"]
    assert ada["usage_date"] == TODAY
    assert ada["cost_kind"] is CostKind.metered_usage
    assert ada["subject_kind"] is CostSubjectKind.member
    assert ada["cost_source"] is CostSource.vendor_reported
    # 1250 cents → $12.50.
    assert ada["cost_usd"] == Decimal("12.50")
    assert isinstance(ada["cost_usd"], Decimal)
    assert ada["seats"] is None
    assert ada["quantity"] is None
    assert ada["tokens_in"] is None
    assert ada["tokens_out"] is None


def test_normalize_zero_spend_member_still_emitted():
    records = _normalize(SPEND_PAYLOAD, TODAY)
    by_member = {r["subject_ref"]: r for r in records}
    alan = by_member["alan@example.com"]
    assert alan["cost_usd"] == Decimal("0.00")


def test_normalize_current_cycle_is_always_provisional():
    records = _normalize(SPEND_PAYLOAD, TODAY)
    for r in records:
        assert r["is_provisional"] is True


def test_normalize_raw_metadata_carries_cents_and_requests():
    records = _normalize(SPEND_PAYLOAD, TODAY)
    by_member = {r["subject_ref"]: r for r in records}
    ada = by_member["ada@example.com"]
    assert ada["raw_metadata"]["spend_cents"] == "1250"
    assert ada["raw_metadata"]["fast_premium_requests"] == 320


def test_normalize_skips_members_without_email():
    payload = {
        "teamMemberSpend": [
            {"userId": 9, "name": "Ghost", "spendCents": 500},
        ]
    }
    records = _normalize(payload, TODAY)
    assert records == []


def test_normalize_handles_empty_payload():
    assert _normalize({}, TODAY) == []
    assert _normalize({"teamMemberSpend": []}, TODAY) == []


def test_connector_provider_and_protocol():
    from app.services.cost.base import CostConnector

    connector = CursorCostConnector()
    assert connector.provider is CostProvider.cursor
    assert isinstance(connector, CostConnector)


def test_connector_is_registered():
    from app.services.cost import registry

    assert CostProvider.cursor in registry.registered_providers()
    assert isinstance(registry.get_connector(CostProvider.cursor), CursorCostConnector)
