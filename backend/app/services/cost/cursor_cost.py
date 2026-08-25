"""Cursor cost connector — per-member on-demand spend from the Teams Admin API.

Pulls per-member spend from the Cursor Teams Admin API and normalises it into
``CostRecord`` dicts (one per team member). The admin API key lives encrypted on
the Integration row and is sent as HTTP **Basic auth** with the key as the
username and an empty password (``Authorization: Basic base64(<key>:)``).

Endpoint used (base ``https://api.cursor.com``):

  POST /teams/spend
      body: {"page": <int>, "pageSize": <int>}  (sortBy/searchTerm optional)
      → ``teamMemberSpend[]`` rows with ``email``, ``name``, ``role``,
        ``userId``, ``spendCents`` (on-demand spend for the **current billing
        cycle**, in *cents*), ``overallSpendCents``, ``fastPremiumRequests``.
        Cycle/pagination: ``subscriptionCycleStart`` (epoch ms),
        ``totalMembers``, ``totalPages``. Docs:
        https://cursor.com/docs/account/teams/admin-api

Mapping notes:
  * The spend endpoint reports the **current billing cycle to date**, not a
    per-day breakdown, so there is no day grain to map onto the ``since``/
    ``until`` window. We attribute the whole-cycle figure to ``today`` and flag
    every record provisional — it keeps moving until the cycle closes. (The
    sibling ``/teams/daily-usage-data`` endpoint has per-day rows but carries
    no cost, so it cannot drive the cost ledger.)
  * ``spendCents`` is *on-demand* spend in cents — the amount billed on top of
    included usage. We convert cents → USD dollars.
  * One record per member: ``subject_kind=member``, ``subject_ref=<email>``.

ALL mapping logic lives in the pure module-level ``_normalize`` helper, fully
unit-tested with a recorded-shape fixture. The live HTTP path in ``fetch_cost``
is a thin wrapper and is **pending validation against a real Cursor admin
key** — it is not exercised by the unit suite.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.models.ai_cost_record import (
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
)
from app.services.cost import registry
from app.services.cost.base import CostRecord
from app.services.crypto import decrypt_token

if TYPE_CHECKING:
    from app.models.integration import Integration

logger = structlog.get_logger()

_BASE_URL = "https://api.cursor.com"
_SPEND_PATH = "/teams/spend"
# ``spendCents`` is in cents.
_CENTS_PER_DOLLAR = Decimal(100)
_PAGE_SIZE = 100
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


# ─── Pure normalisation (fully unit-tested) ──────────────────────────


def _normalize(spend_payload: dict[str, Any], today: date) -> list[CostRecord]:
    """Map a ``/teams/spend`` payload into per-member ``CostRecord`` dicts.

    One record per member, dated ``today`` and flagged provisional (the spend
    figure is the current cycle to date, not a settled daily total). Members
    without an email are skipped — email is the stable subject key. ``spendCents``
    is converted cents → USD dollars.
    """
    records: list[CostRecord] = []
    for member in spend_payload.get("teamMemberSpend") or []:
        email = member.get("email")
        if not email:
            continue
        cents = Decimal(str(member.get("spendCents") or 0))
        record: CostRecord = {
            "usage_date": today,
            "cost_kind": CostKind.metered_usage,
            "subject_kind": CostSubjectKind.member,
            "subject_ref": email,
            "tokens_in": None,
            "tokens_out": None,
            "seats": None,
            "quantity": None,
            "cost_usd": cents / _CENTS_PER_DOLLAR,
            "cost_source": CostSource.vendor_reported,
            # Current-cycle spend keeps moving until the cycle closes.
            "is_provisional": True,
            "raw_metadata": {
                "spend_cents": str(cents),
                "overall_spend_cents": str(member.get("overallSpendCents") or 0),
                "fast_premium_requests": member.get("fastPremiumRequests"),
                "role": member.get("role"),
                "currency": "USD",
            },
        }
        records.append(record)
    return records


# ─── Connector (thin HTTP wrapper; live path pending validation) ─────


class CursorCostConnector:
    """Fetch + normalise Cursor team spend. Satisfies ``CostConnector``."""

    provider: CostProvider = CostProvider.cursor

    async def _get_all_members(
        self,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """Follow ``totalPages`` and concatenate every member row."""
        merged: dict[str, Any] = {"teamMemberSpend": []}
        page = 1
        while True:
            resp = await client.post(
                _SPEND_PATH,
                json={"page": page, "pageSize": _PAGE_SIZE},
            )
            resp.raise_for_status()
            body = resp.json()
            merged["teamMemberSpend"].extend(body.get("teamMemberSpend") or [])
            total_pages = int(body.get("totalPages") or 1)
            if page >= total_pages:
                break
            page += 1
        return merged

    async def fetch_cost(
        self,
        integration: Integration,
        since: date,
        until: date,
    ) -> list[CostRecord]:
        """Fetch + normalise Cursor team spend (async).

        The Cursor spend endpoint has no date-window parameter — it always
        returns the current billing cycle — so ``since``/``until`` are accepted
        for interface parity but not forwarded. Awaited by the cost sync
        orchestrator inside the running event loop.
        """
        if not integration.access_token_encrypted:
            raise ValueError("Cursor integration has no stored admin key")
        admin_key = decrypt_token(integration.access_token_encrypted)
        try:
            json.loads(integration.config_json or "{}")
        except json.JSONDecodeError:
            pass  # config currently has no honoured fields; tolerate junk.

        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            # Cursor admin API uses Basic auth: key as username, empty password.
            auth=(admin_key, ""),
            timeout=_HTTP_TIMEOUT,
        ) as client:
            spend_payload = await self._get_all_members(client)

        today = datetime.now(UTC).date()
        return _normalize(spend_payload, today)


registry.register(CursorCostConnector())
