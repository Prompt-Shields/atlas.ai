"""Anthropic cost connector — the first adapter, proving the framework interface.

Pulls org-level spend + token usage from the Anthropic Admin API and normalises
it into ``CostRecord`` dicts (one per day+model). The admin key (``sk-ant-admin…``)
lives encrypted on the Integration row.

Endpoints used (base ``https://api.anthropic.com``, headers ``x-api-key`` +
``anthropic-version: 2023-06-01``):

  GET /v1/organizations/cost_report
      query: starting_at, ending_at, bucket_width=1d, group_by[]=description
      → time buckets → results[] with ``amount`` (decimal string in *lowest
        currency unit*, i.e. cents for USD), ``model``, ``token_type``.
        Per (day, model) the amount is split across several token_type rows
        which we sum.  Docs:
        https://platform.claude.com/docs/en/api/admin-api/usage-cost/get-cost-report

  GET /v1/organizations/usage_report/messages
      query: starting_at, ending_at, bucket_width=1d, group_by[]=model
      → time buckets → results[] with ``uncached_input_tokens``,
        ``cache_read_input_tokens``, ``cache_creation.*``, ``output_tokens``.
        Docs:
        https://platform.claude.com/docs/en/api/admin-api/usage-cost/get-messages-usage-report

Pagination on both: ``has_more`` + ``next_page`` (echoed back as ``page``).

ALL mapping logic lives in the pure module-level ``_normalize`` /
``_merge_tokens`` helpers, which are fully unit-tested with recorded fixtures.
The live HTTP path in ``fetch_cost`` is a thin wrapper and is **pending
validation against a real admin key** — it is not exercised by the unit suite.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
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

_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_COST_PATH = "/v1/organizations/cost_report"
_USAGE_PATH = "/v1/organizations/usage_report/messages"
# Cost Report ``amount`` is in the lowest currency unit (cents for USD).
_CENTS_PER_DOLLAR = Decimal(100)
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


# ─── Pure normalisation (fully unit-tested) ──────────────────────────


def _bucket_day(bucket: dict[str, Any]) -> date | None:
    """Return the UTC calendar date a time bucket starts on, or None."""
    raw = bucket.get("starting_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _merge_tokens(usage_payload: dict[str, Any]) -> dict[tuple[date, str], tuple[int, int]]:
    """Index the usage report by (day, model) → (tokens_in, tokens_out).

    ``tokens_in`` aggregates every input flavour Anthropic bills for:
    uncached input, cache reads, and cache-creation (1h + 5m).
    """
    out: dict[tuple[date, str], tuple[int, int]] = {}
    for bucket in usage_payload.get("data") or []:
        day = _bucket_day(bucket)
        if day is None:
            continue
        for row in bucket.get("results") or []:
            model = row.get("model")
            if not model:
                continue
            cache = row.get("cache_creation") or {}
            tokens_in = (
                int(row.get("uncached_input_tokens") or 0)
                + int(row.get("cache_read_input_tokens") or 0)
                + int(cache.get("ephemeral_1h_input_tokens") or 0)
                + int(cache.get("ephemeral_5m_input_tokens") or 0)
            )
            tokens_out = int(row.get("output_tokens") or 0)
            key = (day, model)
            prev_in, prev_out = out.get(key, (0, 0))
            out[key] = (prev_in + tokens_in, prev_out + tokens_out)
    return out


def _normalize(
    cost_payload: dict[str, Any],
    usage_payload: dict[str, Any],
    today: date,
) -> list[CostRecord]:
    """Map a Cost Report + Usage Report into ``CostRecord`` dicts.

    One record per (day, model). Cost rows for the same (day, model) — split
    across token_type lines — are summed; ``amount`` (cents) is converted to
    USD dollars. Token counts are merged in from ``usage_payload``. Records
    whose day equals ``today`` are flagged provisional.
    """
    tokens = _merge_tokens(usage_payload)

    # (day, model) → summed cost in cents.
    cost_cents: dict[tuple[date, str], Decimal] = defaultdict(lambda: Decimal(0))
    for bucket in cost_payload.get("data") or []:
        day = _bucket_day(bucket)
        if day is None:
            continue
        for row in bucket.get("results") or []:
            amount_raw = row.get("amount")
            if amount_raw is None:
                continue
            # ``model`` is null for non-token costs (web_search, etc.); bucket
            # those under "" so spend is never silently dropped.
            model = row.get("model") or ""
            cost_cents[(day, model)] += Decimal(str(amount_raw))

    records: list[CostRecord] = []
    for (day, model), cents in sorted(cost_cents.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        tokens_in, tokens_out = tokens.get((day, model), (None, None))
        record: CostRecord = {
            "usage_date": day,
            "cost_kind": CostKind.metered_usage,
            "subject_kind": CostSubjectKind.model,
            "subject_ref": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "seats": None,
            "quantity": None,
            "cost_usd": cents / _CENTS_PER_DOLLAR,
            "cost_source": CostSource.vendor_reported,
            "is_provisional": day == today,
            "raw_metadata": {"amount_cents": str(cents), "currency": "USD"},
        }
        records.append(record)
    return records


# ─── Connector (thin HTTP wrapper; live path pending validation) ─────


class AnthropicCostConnector:
    """Fetch + normalise Anthropic org spend. Satisfies ``CostConnector``."""

    provider: CostProvider = CostProvider.anthropic

    async def _get_all(
        self,
        client: httpx.AsyncClient,
        *,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Follow ``has_more``/``next_page`` and concatenate every bucket."""
        merged: dict[str, Any] = {"data": []}
        page: str | None = None
        while True:
            call_params = dict(params)
            if page:
                call_params["page"] = page
            resp = await client.get(path, params=call_params)
            resp.raise_for_status()
            body = resp.json()
            merged["data"].extend(body.get("data") or [])
            if not body.get("has_more"):
                break
            page = body.get("next_page")
            if not page:
                break
        return merged

    async def fetch_cost(
        self,
        integration: Integration,
        since: date,
        until: date,
    ) -> list[CostRecord]:
        """Fetch + normalise Anthropic cost/usage for the window (async).

        Awaited by the cost sync orchestrator inside the request/worker
        event loop — hence async (no ``asyncio.run`` re-entrancy).
        """
        if not integration.access_token_encrypted:
            raise ValueError("Anthropic integration has no stored admin key")
        admin_key = decrypt_token(integration.access_token_encrypted)
        try:
            json.loads(integration.config_json or "{}")
        except json.JSONDecodeError:
            pass  # config currently has no honoured fields; tolerate junk.

        starting_at = f"{since.isoformat()}T00:00:00Z"
        # ``ending_at`` is exclusive; include the whole ``until`` day.
        ending_at = f"{(until + timedelta(days=1)).isoformat()}T00:00:00Z"
        headers = {
            "x-api-key": admin_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }

        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        ) as client:
            cost_payload = await self._get_all(
                client,
                path=_COST_PATH,
                params={
                    "starting_at": starting_at,
                    "ending_at": ending_at,
                    "bucket_width": "1d",
                    "group_by[]": "description",
                },
            )
            usage_payload = await self._get_all(
                client,
                path=_USAGE_PATH,
                params={
                    "starting_at": starting_at,
                    "ending_at": ending_at,
                    "bucket_width": "1d",
                    "group_by[]": "model",
                },
            )

        today = datetime.now(UTC).date()
        return _normalize(cost_payload, usage_payload, today)


registry.register(AnthropicCostConnector())
