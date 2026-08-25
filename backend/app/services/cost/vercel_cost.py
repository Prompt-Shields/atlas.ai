"""Vercel cost connector — platform infra spend + optional AI Gateway tokens.

Pulls Vercel platform spend (and, when the tenant opts in, AI Gateway model
spend) and normalises both into ``CostRecord`` dicts. The Vercel access token
lives encrypted on the Integration row and is sent as
``Authorization: Bearer <token>``.

The Vercel team id/slug comes from the integration's ``config_json``
(``vercel_team_id`` and/or ``vercel_team_slug``). AI Gateway ingestion is
gated behind ``config_json["vercel_ai_gateway"]`` (default off) and uses a
separate AI-Gateway API key (``config_json["vercel_ai_gateway_key"]``) since
the gateway endpoint authenticates with its own key.

Endpoints used:

  GET https://api.vercel.com/v1/billing/charges
      query: from, to (ISO 8601 UTC; to is exclusive), teamId
      → FOCUS v1.3 charges streamed as JSONL (one JSON object per line). Each
        row carries ``BilledCost`` (USD invoicing amount), ``BillingCurrency``
        ("USD"), ``ChargePeriodStart`` (ISO 8601, the day), ``ServiceName``
        (display name of the product/SKU), ``ConsumedQuantity``/``ConsumedUnit``
        and ``ChargeCategory``. Docs:
        https://vercel.com/docs/rest-api/billing/list-focus-billing-charges

  GET https://ai-gateway.vercel.sh/v1/report   (only if vercel_ai_gateway=True)
      query: start_date, end_date (YYYY-MM-DD, inclusive), group_by=model,
             date_part=day
      → ``results[]`` rows with ``day``, ``model`` (creator/model-name),
        ``total_cost`` (USD; 0 for BYOK), ``input_tokens``, ``output_tokens``.
        Docs:
        https://vercel.com/docs/ai-gateway/capabilities/custom-reporting

Mapping notes:
  * FOCUS charges → ``cost_kind=infra``, ``cost_source=vendor_reported``,
    ``subject_kind=sku``, ``subject_ref=ServiceName``. Rows sharing a
    (day, service) are summed; ``BilledCost`` is already in USD dollars.
    ``ConsumedQuantity`` becomes ``quantity``.
  * AI Gateway rows → ``cost_kind=metered_usage``, ``cost_source=vendor_reported``,
    ``subject_kind=other``, ``subject_ref=<model>``, with token counts. These
    appear only when the tenant enables ``vercel_ai_gateway``.
  * Records whose day equals ``today`` are flagged provisional.

ALL mapping logic lives in the pure module-level ``_normalize`` helper, fully
unit-tested with recorded-shape fixtures. The ``fetch_cost`` HTTP path (incl.
JSONL parsing) is a thin wrapper and is **pending validation against a real
Vercel token + AI Gateway key** — it is not exercised by the unit suite.
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

_BILLING_BASE_URL = "https://api.vercel.com"
_BILLING_PATH = "/v1/billing/charges"
_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh"
_GATEWAY_REPORT_PATH = "/v1/report"
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


# ─── Pure normalisation (fully unit-tested) ──────────────────────────


def _charge_day(charge: dict[str, Any]) -> date | None:
    """Return the day a FOCUS charge covers from ``ChargePeriodStart``."""
    raw = charge.get("ChargePeriodStart")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _gateway_day(row: dict[str, Any]) -> date | None:
    """Return the day an AI Gateway report row covers from ``day``."""
    raw = row.get("day")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _normalize_billing(charges: list[dict[str, Any]], today: date) -> list[CostRecord]:
    """Map FOCUS billing charges → infra ``CostRecord`` dicts.

    One record per (day, ServiceName); rows sharing that key are summed.
    ``BilledCost`` is already in USD dollars.
    """
    # (day, service) → [summed cost, summed quantity].
    cost: dict[tuple[date, str], Decimal] = defaultdict(lambda: Decimal(0))
    qty: dict[tuple[date, str], Decimal] = defaultdict(lambda: Decimal(0))
    seen_qty: set[tuple[date, str]] = set()
    for charge in charges or []:
        day = _charge_day(charge)
        if day is None:
            continue
        amount = charge.get("BilledCost")
        if amount is None:
            continue
        service = charge.get("ServiceName") or ""
        key = (day, service)
        cost[key] += Decimal(str(amount))
        consumed = charge.get("ConsumedQuantity")
        if consumed is not None:
            qty[key] += Decimal(str(consumed))
            seen_qty.add(key)

    records: list[CostRecord] = []
    for (day, service), dollars in sorted(cost.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        key = (day, service)
        record: CostRecord = {
            "usage_date": day,
            "cost_kind": CostKind.infra,
            "subject_kind": CostSubjectKind.sku,
            "subject_ref": service,
            "tokens_in": None,
            "tokens_out": None,
            "seats": None,
            "quantity": qty[key] if key in seen_qty else None,
            "cost_usd": dollars,
            "cost_source": CostSource.vendor_reported,
            "is_provisional": day == today,
            "raw_metadata": {"billed_cost_usd": str(dollars), "currency": "USD"},
        }
        records.append(record)
    return records


def _normalize_gateway(report: dict[str, Any], today: date) -> list[CostRecord]:
    """Map an AI Gateway report (group_by=model) → metered ``CostRecord`` dicts.

    One record per (day, model). ``total_cost`` is already in USD dollars.
    """
    records: list[CostRecord] = []
    for row in report.get("results") or []:
        day = _gateway_day(row)
        if day is None:
            continue
        model = row.get("model") or ""
        cost_raw = row.get("total_cost")
        cost_usd = Decimal(str(cost_raw)) if cost_raw is not None else Decimal(0)
        tin = row.get("input_tokens")
        tout = row.get("output_tokens")
        record: CostRecord = {
            "usage_date": day,
            "cost_kind": CostKind.metered_usage,
            "subject_kind": CostSubjectKind.other,
            "subject_ref": model,
            "tokens_in": int(tin) if tin is not None else None,
            "tokens_out": int(tout) if tout is not None else None,
            "seats": None,
            "quantity": None,
            "cost_usd": cost_usd,
            "cost_source": CostSource.vendor_reported,
            "is_provisional": day == today,
            "raw_metadata": {
                "total_cost_usd": str(cost_usd),
                "request_count": row.get("request_count"),
                "source": "ai_gateway",
                "currency": "USD",
            },
        }
        records.append(record)
    return records


def _normalize(
    charges: list[dict[str, Any]],
    gateway_report: dict[str, Any] | None,
    today: date,
) -> list[CostRecord]:
    """Map Vercel billing charges + optional AI Gateway report into records.

    ``gateway_report`` is ``None`` when the tenant has not enabled AI Gateway
    ingestion, in which case only infra records are produced.
    """
    records = _normalize_billing(charges, today)
    if gateway_report is not None:
        records.extend(_normalize_gateway(gateway_report, today))
    return records


# ─── Connector (thin HTTP wrapper; live path pending validation) ─────


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON into a list of objects (blank lines skipped)."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


class VercelCostConnector:
    """Fetch + normalise Vercel infra + AI Gateway spend. Satisfies ``CostConnector``."""

    provider: CostProvider = CostProvider.vercel

    async def fetch_cost(
        self,
        integration: Integration,
        since: date,
        until: date,
    ) -> list[CostRecord]:
        """Fetch + normalise Vercel cost for the window (async).

        Always pulls FOCUS billing charges; additionally pulls the AI Gateway
        report when ``config_json["vercel_ai_gateway"]`` is truthy. Awaited by
        the cost sync orchestrator inside the running event loop.
        """
        if not integration.access_token_encrypted:
            raise ValueError("Vercel integration has no stored access token")
        token = decrypt_token(integration.access_token_encrypted)
        cfg = json.loads(integration.config_json or "{}")
        team_id = cfg.get("vercel_team_id")
        team_slug = cfg.get("vercel_team_slug")

        # FOCUS billing: ``to`` is exclusive — add a day to include all of ``until``.
        billing_params: dict[str, Any] = {
            "from": f"{since.isoformat()}T00:00:00Z",
            "to": f"{(until + timedelta(days=1)).isoformat()}T00:00:00Z",
        }
        if team_id:
            billing_params["teamId"] = team_id
        if team_slug:
            billing_params["slug"] = team_slug

        async with httpx.AsyncClient(
            base_url=_BILLING_BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT,
        ) as client:
            billing_resp = await client.get(_BILLING_PATH, params=billing_params)
            billing_resp.raise_for_status()
            charges = _parse_jsonl(billing_resp.text)

        gateway_report: dict[str, Any] | None = None
        if cfg.get("vercel_ai_gateway"):
            gateway_key = cfg.get("vercel_ai_gateway_key") or token
            async with httpx.AsyncClient(
                base_url=_GATEWAY_BASE_URL,
                headers={"Authorization": f"Bearer {gateway_key}"},
                timeout=_HTTP_TIMEOUT,
            ) as gw_client:
                gw_resp = await gw_client.get(
                    _GATEWAY_REPORT_PATH,
                    params={
                        "start_date": since.isoformat(),
                        "end_date": until.isoformat(),
                        "group_by": "model",
                        "date_part": "day",
                    },
                )
                gw_resp.raise_for_status()
                gateway_report = gw_resp.json()

        today = datetime.now(UTC).date()
        return _normalize(charges, gateway_report, today)


registry.register(VercelCostConnector())
