"""GitHub Copilot cost connector — derived seat-subscription spend.

Copilot bills per *seat* (a flat monthly price), not per metered token, and
GitHub does **not** expose a dollar figure over the API. We therefore *derive*
cost = ``seats x price``, where ``seats`` is the org's billed seat count and
``price`` is the per-seat monthly price configured on the integration (or a
documented module-level default — see ``DEFAULT_SEAT_PRICE_USD``).

The org slug comes from the integration's ``config_json`` (``github_org``).
The access token (a PAT / GitHub App token with ``manage_billing:copilot`` or
``read:org``) lives encrypted on the Integration row and is sent as
``Authorization: Bearer <token>``.

Endpoints used (base ``https://api.github.com``, header
``X-GitHub-Api-Version: 2026-03-10``, ``Accept: application/vnd.github+json``):

  GET /orgs/{org}/copilot/billing
      → ``seat_breakdown.total`` — the number of seats currently billed (a
        point-in-time snapshot, not a per-day series), plus ``plan_type`` and
        ``seat_management_setting``. Docs:
        https://docs.github.com/en/rest/copilot/copilot-user-management

  GET /orgs/{org}/copilot/metrics
      query: since, until (ISO 8601; max 100 days)
      → an array of per-day objects with ``date``, ``total_active_users`` and
        ``total_engaged_users``. We attach ``total_engaged_users`` as the
        record ``quantity`` (a usage signal alongside the seat cost). Docs:
        https://docs.github.com/en/rest/copilot/copilot-metrics

Mapping notes:
  * Billing is a snapshot; metrics is the per-day series. We emit one
    ``seat_subscription`` record per metrics day, each carrying the current
    seat count x price and that day's engaged-user ``quantity``. If the metrics
    array is empty (e.g. a brand-new org, or metrics disabled) we still emit a
    single seat record dated ``today`` so the subscription is never dropped.
  * ``cost_source=derived_seats`` and we NEVER emit ``cost_usd=0`` when
    ``seats > 0`` — the documented default price guards against that.
  * The seat price is a *configured assumption*, not a vendor-reported figure;
    the live HTTP path and the price are **pending validation against a real
    org token + the org's actual Copilot plan price**.

ALL mapping logic lives in the pure module-level ``_normalize`` helper, fully
unit-tested with recorded-shape fixtures (the configured-price and
default-fallback paths both covered). The ``fetch_cost`` HTTP path is a thin
wrapper not exercised by the unit suite.
"""

from __future__ import annotations

import json
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

_BASE_URL = "https://api.github.com"
_GITHUB_API_VERSION = "2026-03-10"
_BILLING_PATH = "/orgs/{org}/copilot/billing"
_METRICS_PATH = "/orgs/{org}/copilot/metrics"
# Copilot Business list price per seat / month (USD) as of 2025. The tenant's
# real price can differ (Enterprise, negotiated, annual) so it is overridable
# via ``config_json["copilot_seat_price_usd"]`` — this is only the fallback.
DEFAULT_SEAT_PRICE_USD = Decimal("19.00")
# The subject for a seat subscription is the SKU itself, not a person.
_SUBJECT_REF = "copilot_business_seats"
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


# ─── Pure normalisation (fully unit-tested) ──────────────────────────


def _metrics_day(row: dict[str, Any]) -> date | None:
    """Return the calendar date a Copilot metrics row covers, or None."""
    raw = row.get("date")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _seat_record(
    *,
    day: date,
    seats: int,
    price: Decimal,
    quantity: Decimal | None,
    today: date,
    raw_extra: dict[str, Any],
) -> CostRecord:
    """Build one ``seat_subscription`` CostRecord (derived cost = seats x price)."""
    return {
        "usage_date": day,
        "cost_kind": CostKind.seat_subscription,
        "subject_kind": CostSubjectKind.sku,
        "subject_ref": _SUBJECT_REF,
        "tokens_in": None,
        "tokens_out": None,
        "seats": seats,
        "quantity": quantity,
        "cost_usd": Decimal(seats) * price,
        "cost_source": CostSource.derived_seats,
        "is_provisional": day == today,
        "raw_metadata": {
            "seats": seats,
            "seat_price_usd": str(price),
            **raw_extra,
        },
    }


def _normalize(
    billing_payload: dict[str, Any],
    metrics_payload: list[dict[str, Any]],
    price: Decimal,
    today: date,
) -> list[CostRecord]:
    """Map Copilot billing + metrics into derived seat ``CostRecord`` dicts.

    ``seats`` is taken from ``seat_breakdown.total`` (a snapshot). One record
    is emitted per metrics day, each = seats x ``price`` with that day's
    ``total_engaged_users`` as ``quantity``. When metrics is empty, a single
    record dated ``today`` is emitted so the subscription is never dropped.
    """
    breakdown = billing_payload.get("seat_breakdown") or {}
    seats = int(breakdown.get("total") or 0)
    plan_type = billing_payload.get("plan_type")
    base_meta = {"plan_type": plan_type} if plan_type is not None else {}

    rows: list[tuple[date, Decimal | None, dict[str, Any]]] = []
    for row in metrics_payload or []:
        day = _metrics_day(row)
        if day is None:
            continue
        engaged = row.get("total_engaged_users")
        quantity = Decimal(str(engaged)) if engaged is not None else None
        rows.append((day, quantity, {"total_engaged_users": engaged}))

    if not rows:
        # No per-day metrics — emit one record for today so the seat cost lands.
        return [
            _seat_record(
                day=today,
                seats=seats,
                price=price,
                quantity=None,
                today=today,
                raw_extra=base_meta,
            )
        ]

    records: list[CostRecord] = []
    for day, quantity, extra in sorted(rows, key=lambda r: r[0]):
        records.append(
            _seat_record(
                day=day,
                seats=seats,
                price=price,
                quantity=quantity,
                today=today,
                raw_extra={**base_meta, **extra},
            )
        )
    return records


# ─── Connector (thin HTTP wrapper; live path pending validation) ─────


class CopilotCostConnector:
    """Fetch billing + metrics, derive seat cost. Satisfies ``CostConnector``."""

    provider: CostProvider = CostProvider.github_copilot

    async def fetch_cost(
        self,
        integration: Integration,
        since: date,
        until: date,
    ) -> list[CostRecord]:
        """Fetch Copilot billing + metrics and derive seat cost (async).

        Awaited by the cost sync orchestrator inside the running event loop.
        The org slug and (optional) seat price come from ``config_json``.
        """
        if not integration.access_token_encrypted:
            raise ValueError("GitHub Copilot integration has no stored token")
        token = decrypt_token(integration.access_token_encrypted)
        cfg = json.loads(integration.config_json or "{}")
        org = cfg.get("github_org")
        if not org:
            raise ValueError("GitHub Copilot integration config is missing 'github_org'")
        price_raw = cfg.get("copilot_seat_price_usd")
        price = Decimal(str(price_raw)) if price_raw is not None else DEFAULT_SEAT_PRICE_USD

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        }

        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        ) as client:
            billing_resp = await client.get(_BILLING_PATH.format(org=org))
            billing_resp.raise_for_status()
            billing_payload = billing_resp.json()

            metrics_resp = await client.get(
                _METRICS_PATH.format(org=org),
                params={
                    "since": f"{since.isoformat()}T00:00:00Z",
                    # ``until`` is inclusive of the whole final day.
                    "until": f"{(until + timedelta(days=1)).isoformat()}T00:00:00Z",
                },
            )
            metrics_resp.raise_for_status()
            metrics_payload = metrics_resp.json()
            if not isinstance(metrics_payload, list):
                metrics_payload = []

        today = datetime.now(UTC).date()
        return _normalize(billing_payload, metrics_payload, price, today)


registry.register(CopilotCostConnector())
