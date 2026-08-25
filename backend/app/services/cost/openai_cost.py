"""OpenAI cost connector — org-level API spend + token usage.

Pulls OpenAI **API** spend (NOT ChatGPT seats) and token usage from the
OpenAI Organization admin API and normalises it into ``CostRecord`` dicts
(one per day+line-item). The admin key (``sk-admin…``) lives encrypted on the
Integration row and is sent as ``Authorization: Bearer <admin key>``.

Endpoints used (base ``https://api.openai.com``):

  GET /v1/organization/costs
      query: start_time, end_time (Unix seconds), bucket_width=1d,
             group_by[]=line_item
      → ``data[]`` time buckets (Unix-second ``start_time``/``end_time``) →
        ``results[]`` of ``organization.costs.result`` with
        ``amount.value`` (a number in **US DOLLARS**, not cents),
        ``amount.currency`` (e.g. "usd"), and ``line_item``. Docs:
        https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs

  GET /v1/organization/usage/completions
      query: start_time, end_time (Unix seconds), bucket_width=1d,
             group_by[]=model
      → ``data[]`` time buckets → ``results[]`` of
        ``organization.usage.completions.result`` with ``input_tokens``
        (includes ``input_cached_tokens``), ``output_tokens`` and ``model``.
        Docs:
        https://developers.openai.com/api/reference/resources/organization/subresources/usage/methods/get_completions

Pagination on both: ``has_more`` + ``next_page`` (a cursor echoed back as the
``page`` query param).

Mapping notes:
  * The Costs API groups by *line item*, the Usage API by *model*; they do not
    share a join key. We key cost records on (day, line_item) and attach a
    day's per-model token totals to every line item whose text contains that
    model name (line items are typically ``"<model>, input"`` etc.). Line
    items that match no model (e.g. tool-call costs) keep ``tokens_*=None``.
  * ``amount.value`` is already in dollars — UNLIKE Anthropic's cost report
    which is in cents — so there is no currency-unit conversion here.

ALL mapping logic lives in the pure module-level ``_normalize`` /
``_merge_tokens`` helpers, fully unit-tested with recorded fixtures. The live
HTTP path in ``fetch_cost`` is a thin wrapper and is **pending validation
against a real admin key** — it is not exercised by the unit suite.
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

_BASE_URL = "https://api.openai.com"
_COST_PATH = "/v1/organization/costs"
_USAGE_PATH = "/v1/organization/usage/completions"
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


# ─── Pure normalisation (fully unit-tested) ──────────────────────────


def _bucket_day(bucket: dict[str, Any]) -> date | None:
    """Return the UTC calendar date a Unix-second time bucket starts on."""
    raw = bucket.get("start_time")
    if not isinstance(raw, (int, float)):
        return None
    return datetime.fromtimestamp(raw, tz=UTC).date()


def _merge_tokens(usage_payload: dict[str, Any]) -> dict[tuple[date, str], tuple[int, int]]:
    """Index the usage report by (day, model) → (tokens_in, tokens_out).

    ``input_tokens`` already includes cached input, so we use it directly.
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
            tokens_in = int(row.get("input_tokens") or 0)
            tokens_out = int(row.get("output_tokens") or 0)
            key = (day, model)
            prev_in, prev_out = out.get(key, (0, 0))
            out[key] = (prev_in + tokens_in, prev_out + tokens_out)
    return out


def _tokens_for_line_item(
    day: date,
    line_item: str,
    tokens: dict[tuple[date, str], tuple[int, int]],
) -> tuple[int | None, int | None]:
    """Best-effort attach a day's model token totals to a cost line item.

    A line item like ``"gpt-4o-2024-08-06, input"`` is matched to the usage
    model ``"gpt-4o-2024-08-06"`` by substring. Returns ``(None, None)`` when
    no model name appears in the line item.
    """
    for (tday, model), (t_in, t_out) in tokens.items():
        if tday == day and model and model in line_item:
            return t_in, t_out
    return None, None


def _normalize(
    cost_payload: dict[str, Any],
    usage_payload: dict[str, Any],
    today: date,
) -> list[CostRecord]:
    """Map a Costs report + Completions usage report into ``CostRecord`` dicts.

    One record per (day, line_item). Cost rows sharing a (day, line_item) are
    summed; ``amount.value`` is already in USD dollars (no conversion). Token
    counts are attached from ``usage_payload`` by model-substring match.
    Records whose day equals ``today`` are flagged provisional.
    """
    tokens = _merge_tokens(usage_payload)

    # (day, line_item) → summed cost in dollars.
    cost_usd: dict[tuple[date, str], Decimal] = defaultdict(lambda: Decimal(0))
    for bucket in cost_payload.get("data") or []:
        day = _bucket_day(bucket)
        if day is None:
            continue
        for row in bucket.get("results") or []:
            amount = row.get("amount") or {}
            value = amount.get("value")
            if value is None:
                continue
            # ``line_item`` is null when not grouped; bucket those under ""
            # so spend is never silently dropped.
            line_item = row.get("line_item") or ""
            cost_usd[(day, line_item)] += Decimal(str(value))

    records: list[CostRecord] = []
    for (day, line_item), dollars in sorted(cost_usd.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        tokens_in, tokens_out = _tokens_for_line_item(day, line_item, tokens)
        record: CostRecord = {
            "usage_date": day,
            "cost_kind": CostKind.metered_usage,
            "subject_kind": CostSubjectKind.model,
            "subject_ref": line_item,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "seats": None,
            "quantity": None,
            "cost_usd": dollars,
            "cost_source": CostSource.vendor_reported,
            "is_provisional": day == today,
            "raw_metadata": {"amount_usd": str(dollars), "currency": "usd"},
        }
        records.append(record)
    return records


# ─── Connector (thin HTTP wrapper; live path pending validation) ─────


class OpenAICostConnector:
    """Fetch + normalise OpenAI org API spend. Satisfies ``CostConnector``."""

    provider: CostProvider = CostProvider.openai

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
        """Fetch + normalise OpenAI cost/usage for the window (async).

        Awaited by the cost sync orchestrator inside the request/worker event
        loop — hence async (no ``asyncio.run`` re-entrancy).
        """
        if not integration.access_token_encrypted:
            raise ValueError("OpenAI integration has no stored admin key")
        admin_key = decrypt_token(integration.access_token_encrypted)
        try:
            json.loads(integration.config_json or "{}")
        except json.JSONDecodeError:
            pass  # config currently has no honoured fields; tolerate junk.

        start_time = int(datetime.combine(since, datetime.min.time(), tzinfo=UTC).timestamp())
        # ``end_time`` is exclusive; include the whole ``until`` day.
        end_time = int(
            datetime.combine(until + timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp()
        )
        headers = {"Authorization": f"Bearer {admin_key}"}

        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        ) as client:
            cost_payload = await self._get_all(
                client,
                path=_COST_PATH,
                params={
                    "start_time": start_time,
                    "end_time": end_time,
                    "bucket_width": "1d",
                    "group_by[]": "line_item",
                    "limit": 180,
                },
            )
            usage_payload = await self._get_all(
                client,
                path=_USAGE_PATH,
                params={
                    "start_time": start_time,
                    "end_time": end_time,
                    "bucket_width": "1d",
                    "group_by[]": "model",
                    "limit": 31,
                },
            )

        today = datetime.now(UTC).date()
        return _normalize(cost_payload, usage_payload, today)


registry.register(OpenAICostConnector())
