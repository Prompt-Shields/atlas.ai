"""Microsoft Defender for Cloud Apps sync — cloud-app discovery.

Source: Graph `/security/alerts_v2` filtered to alerts where the
detection source is Microsoft Cloud App Security. v0.2 will switch
to the MCAS REST API `/api/v1/discovery/discovered_apps` for the
real cloud-app inventory; today we ingest CASB-generated alerts and
let the Discover page render them as detections.

`config_json` honoured fields:
  - app_filter:     list[str]  — restrict to specific app domains
  - severity_floor: 'low'|'medium'|'high' — drop alerts below
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
from app.models.microsoft_sync_outputs import CloudAppDetection
from app.services.microsoft_graph import (
    GraphAPIError,
    graph_get,
    graph_get_url,
)
from app.services.microsoft_oauth import MicrosoftOAuthError
from app.services.microsoft_token_refresh import get_valid_access_token

logger = structlog.get_logger()


CASB_PRODUCT_NAMES = {
    "Microsoft Cloud App Security",
    "Microsoft Defender for Cloud Apps",
}

SEVERITY_RANK = {"informational": 0, "low": 1, "medium": 2, "high": 3}


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


def normalise_detection(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Graph alerts_v2 entry → CloudAppDetection fields."""
    product = raw.get("productName") or ""
    if isinstance(product, str) and product:
        if product not in CASB_PRODUCT_NAMES:
            return None

    # App identity — alerts_v2 packs this in `evidence[]` of type
    # `cloudApplicationEvidence`. Fall back to mainServiceSource fields
    # when present.
    app_name = None
    app_domain = None
    risk_score = None
    user_upn = None

    evidence = raw.get("evidence") or []
    if isinstance(evidence, list):
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            otype = ev.get("@odata.type", "")
            if "cloudApplicationEvidence" in otype or "applicationEvidence" in otype:
                app_name = app_name or ev.get("displayName")
                app_domain = app_domain or ev.get("instanceName")
                try:
                    risk_score = (
                        risk_score if risk_score is not None else int(ev.get("riskScore") or 0)
                    )
                except (TypeError, ValueError):
                    pass
            if "userEvidence" in otype:
                user_account = ev.get("userAccount") or {}
                if isinstance(user_account, dict):
                    user_upn = user_upn or user_account.get("userPrincipalName")

    if not app_name:
        return None

    return {
        "external_detection_id": str(raw.get("id") or "").strip(),
        "app_name": app_name[:255],
        "app_domain": _lower_or_none(app_domain),
        "app_category": _strip_or_none(raw.get("category")),
        "risk_score": risk_score,
        "is_sanctioned": False,
        "user_principal_name": _lower_or_none(user_upn),
        "first_seen_at": _parse_iso(raw.get("firstActivityDateTime")),
        "last_seen_at": _parse_iso(raw.get("lastActivityDateTime") or raw.get("createdDateTime")),
    }


def _passes_filters(integration: Integration, fields: dict[str, Any]) -> bool:
    """Apply config_json's app_filter + severity_floor."""
    try:
        cfg = json.loads(integration.config_json or "{}")
    except json.JSONDecodeError:
        cfg = {}

    app_filter = cfg.get("app_filter") or []
    if app_filter and fields.get("app_domain"):
        if fields["app_domain"] not in app_filter:
            return False

    severity_floor = (cfg.get("severity_floor") or "").lower()
    if severity_floor in SEVERITY_RANK and fields.get("severity"):
        rank_event = SEVERITY_RANK.get(fields["severity"], 0)
        if rank_event < SEVERITY_RANK[severity_floor]:
            return False
    return True


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
            Integration.provider == IntegrationProvider.MICROSOFT_DEFENDER_CASB,
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
                "defender_sync_failed",
                integration_id=str(integration.id),
                error=str(exc),
            )
    return processed


async def _sync_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
) -> None:
    token = await get_valid_access_token(db, client, integration)
    now = datetime.now(UTC)

    since = (
        integration.last_synced_at - timedelta(minutes=5)
        if integration.last_synced_at
        else now - timedelta(days=30)
    )

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
                fields = normalise_detection(raw)
                if fields is None:
                    continue
                if not _passes_filters(integration, fields):
                    continue
                await _upsert_detection(db, integration, fields, now)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "defender_sync_skip_detection",
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
        "defender_sync_complete",
        integration_id=str(integration.id),
        detections=count,
    )


async def _upsert_detection(
    db: AsyncSession,
    integration: Integration,
    fields: dict[str, Any],
    now: datetime,
) -> None:
    eid = fields["external_detection_id"]
    if not eid:
        return
    existing = (
        await db.execute(
            select(CloudAppDetection).where(
                CloudAppDetection.tenant_id == integration.tenant_id,
                CloudAppDetection.integration_id == integration.id,
                CloudAppDetection.external_detection_id == eid,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            CloudAppDetection(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                external_detection_id=eid,
                app_name=fields["app_name"],
                app_domain=fields["app_domain"],
                app_category=fields["app_category"],
                risk_score=fields["risk_score"],
                is_sanctioned=fields["is_sanctioned"],
                user_principal_name=fields["user_principal_name"],
                event_count_30d=1,
                first_seen_at=fields["first_seen_at"],
                last_seen_at=fields["last_seen_at"],
                last_synced_at=now,
            )
        )
    else:
        existing.app_category = fields["app_category"]
        existing.risk_score = fields["risk_score"]
        existing.user_principal_name = fields["user_principal_name"]
        existing.event_count_30d = (existing.event_count_30d or 0) + 1
        if fields["last_seen_at"]:
            existing.last_seen_at = fields["last_seen_at"]
        existing.last_synced_at = now
