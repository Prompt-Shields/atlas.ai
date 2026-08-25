"""Microsoft Purview sync — DLP / classification audit events.

Source: Graph `/security/alerts_v2` filtered to alerts where the
detection source is Microsoft Purview / Information Protection.
v0.2 will switch to the M365 Management Activity API for richer
DLP context; the Integration row + table shape stay identical.

`config_json` honoured fields (set via /dashboard/integrations/[provider]):
  - classifier_ids: list[str]  — restrict to specific classifiers
  - event_lookback_days: str   — initial backfill window (default 30)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.microsoft_sync_outputs import PurviewEvent
from app.services.microsoft_graph import (
    GraphAPIError,
    graph_get,
    graph_get_url,
)
from app.services.microsoft_oauth import MicrosoftOAuthError
from app.services.microsoft_token_refresh import get_valid_access_token

logger = structlog.get_logger()


PURVIEW_PRODUCT_VENDORS = {"Microsoft Purview", "Microsoft Information Protection"}


def _strip_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _lower_or_none(value: Any) -> str | None:
    s = _strip_or_none(value)
    return s.lower() if s else None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ─── Normalisation ───────────────────────────────────────────────────


def normalise_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Graph alerts_v2 entry → PurviewEvent fields.

    Returns None for non-Purview alerts so the caller can skip.
    """
    product = raw.get("productName") or raw.get("vendorInformation") or {}
    if isinstance(product, dict):
        product_name = product.get("product") or product.get("provider")
    else:
        product_name = product
    if product_name and product_name not in PURVIEW_PRODUCT_VENDORS:
        return None  # Not a Purview alert.

    detected = _parse_iso(
        raw.get("createdDateTime") or raw.get("firstActivityDateTime"),
    )
    if detected is None:
        return None

    return {
        "external_event_id": str(raw.get("id") or "").strip(),
        "event_type": _strip_or_none(raw.get("category")) or "dlp_match",
        "severity": _lower_or_none(raw.get("severity")),
        "title": (raw.get("title") or "Purview event")[:500],
        "description": _strip_or_none(raw.get("description")),
        "actor_user_principal_name": _lower_or_none(
            (raw.get("actorDisplayName") if isinstance(raw, dict) else None)
            or _first_user_upn(raw),
        ),
        "classifier_id": _strip_or_none(raw.get("classifierName"))
        or _strip_or_none(raw.get("classifierId")),
        "raw_payload_json": json.dumps(raw)[:60_000],
        "detected_at": detected,
    }


def _first_user_upn(raw: dict[str, Any]) -> str | None:
    """Best-effort extraction of an actor UPN from the alert payload."""
    evidence = raw.get("evidence") or []
    if not isinstance(evidence, list):
        return None
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        upn = ev.get("userAccount") or {}
        if isinstance(upn, dict) and upn.get("userPrincipalName"):
            return upn["userPrincipalName"]
    return None


# ─── Public entry point ──────────────────────────────────────────────


async def sync_pending(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    batch_size: int = 50,
    stale_after: timedelta = timedelta(hours=2),
) -> int:
    cutoff = datetime.now(UTC) - stale_after
    q = (
        select(Integration)
        .where(
            Integration.provider == IntegrationProvider.MICROSOFT_PURVIEW,
            Integration.status == IntegrationStatus.CONNECTED,
            Integration.is_active.is_(True),
        )
        .order_by(Integration.last_synced_at.asc().nulls_first())
        .limit(batch_size)
    )
    rows = list((await db.execute(q)).scalars().all())
    candidates = [r for r in rows if r.last_synced_at is None or r.last_synced_at < cutoff]

    processed = 0
    for integration in candidates:
        try:
            await _sync_one(db, client, integration)
            processed += 1
        except (
            GraphAPIError,
            MicrosoftOAuthError,
            httpx.HTTPError,
            ValueError,
        ) as exc:
            integration.last_error = f"{type(exc).__name__}: {exc}"[:500]
            await db.commit()
            logger.warning(
                "purview_sync_failed",
                integration_id=str(integration.id),
                error=str(exc),
            )
    return processed


def _lookback_days(integration: Integration) -> int:
    try:
        cfg = json.loads(integration.config_json or "{}")
    except json.JSONDecodeError:
        cfg = {}
    raw = cfg.get("event_lookback_days")
    try:
        return max(1, int(raw)) if raw else 30
    except (TypeError, ValueError):
        return 30


async def _sync_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
) -> None:
    token = await get_valid_access_token(db, client, integration)
    now = datetime.now(UTC)

    # Use last_synced_at as a watermark on subsequent runs; first run
    # uses the configured lookback window.
    if integration.last_synced_at is None:
        since = now - timedelta(days=_lookback_days(integration))
    else:
        since = integration.last_synced_at - timedelta(minutes=5)

    count = 0
    page = await graph_get(
        client,
        token=token,
        path="/security/alerts_v2",
        params={
            "$filter": f"createdDateTime ge {since.isoformat()}",
            "$top": 100,
        },
    )
    while True:
        for raw in page.get("value", []):
            try:
                fields = normalise_event(raw)
                if fields is None:
                    continue
                await _upsert_event(db, integration, fields, now)
                count += 1
            except Exception as exc:  # noqa: BLE001 — skip bad rows
                logger.warning(
                    "purview_sync_skip_event",
                    integration_id=str(integration.id),
                    error=str(exc),
                )
        next_url = page.get("@odata.nextLink")
        if not next_url:
            break
        page = await graph_get_url(client, token=token, url=next_url)

    integration.last_synced_at = now
    integration.last_error = None
    await db.commit()
    logger.info(
        "purview_sync_complete",
        integration_id=str(integration.id),
        events=count,
    )


async def _upsert_event(
    db: AsyncSession,
    integration: Integration,
    fields: dict[str, Any],
    now: datetime,
) -> None:
    eid = fields["external_event_id"]
    if not eid:
        return
    existing = (
        await db.execute(
            select(PurviewEvent).where(
                PurviewEvent.tenant_id == integration.tenant_id,
                PurviewEvent.integration_id == integration.id,
                PurviewEvent.external_event_id == eid,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            PurviewEvent(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                external_event_id=eid,
                event_type=fields["event_type"],
                severity=fields["severity"],
                title=fields["title"],
                description=fields["description"],
                actor_user_principal_name=fields["actor_user_principal_name"],
                classifier_id=fields["classifier_id"],
                raw_payload_json=fields["raw_payload_json"],
                detected_at=fields["detected_at"],
                last_synced_at=now,
            )
        )
    else:
        # Events are mostly immutable; update last_synced_at only.
        existing.last_synced_at = now
