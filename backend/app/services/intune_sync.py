"""Microsoft Intune sync — managed device roster.

Pulls `/deviceManagement/managedDevices` for every connected Intune
integration. Upserts ManagedDevice rows.

Shares the token-refresh + per-integration loop pattern with
`microsoft_sync.py` (Entra). When a third Microsoft sync ships,
factor the common bits into a base class.
"""

from __future__ import annotations

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
from app.models.managed_device import ManagedDevice
from app.services.microsoft_graph import (
    GraphAPIError,
    graph_get,
    graph_get_url,
)
from app.services.microsoft_oauth import MicrosoftOAuthError
from app.services.microsoft_token_refresh import get_valid_access_token

logger = structlog.get_logger()


DEVICE_SELECT_FIELDS = (
    "id,deviceName,operatingSystem,osVersion,complianceState,userPrincipalName,lastSyncDateTime"
)


# ─── Normalisation ───────────────────────────────────────────────────


def normalise_device(raw: dict[str, Any]) -> dict[str, Any]:
    last_sync = raw.get("lastSyncDateTime")
    parsed_last_sync = None
    if isinstance(last_sync, str) and last_sync:
        try:
            parsed_last_sync = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
        except ValueError:
            parsed_last_sync = None
    return {
        "external_device_id": str(raw.get("id") or "").strip(),
        "device_name": (raw.get("deviceName") or "").strip() or "(unnamed)",
        "operating_system": _strip_or_none(raw.get("operatingSystem")),
        "os_version": _strip_or_none(raw.get("osVersion")),
        "compliance_state": _strip_or_none(raw.get("complianceState")),
        "enrolled_user_email": _lower_or_none(raw.get("userPrincipalName")),
        "last_sync_to_mdm_at": parsed_last_sync,
    }


def _strip_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _lower_or_none(value: Any) -> str | None:
    s = _strip_or_none(value)
    return s.lower() if s else None


# ─── Public entry point ──────────────────────────────────────────────


async def sync_pending(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    batch_size: int = 50,
    stale_after: timedelta = timedelta(hours=6),
) -> int:
    cutoff = datetime.now(UTC) - stale_after
    q = (
        select(Integration)
        .where(
            Integration.provider == IntegrationProvider.MICROSOFT_INTUNE,
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
                "intune_sync_failed",
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

    count = 0
    page = await graph_get(
        client,
        token=token,
        path="/deviceManagement/managedDevices",
        params={"$select": DEVICE_SELECT_FIELDS, "$top": 100},
    )
    while True:
        for raw in page.get("value", []):
            try:
                await _upsert_device(db, integration, raw, now)
                count += 1
            except Exception as exc:  # noqa: BLE001 — skip bad rows
                logger.warning(
                    "intune_sync_skip_device",
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
        "intune_sync_complete",
        integration_id=str(integration.id),
        devices=count,
    )


async def _upsert_device(
    db: AsyncSession,
    integration: Integration,
    raw: dict[str, Any],
    now: datetime,
) -> None:
    fields = normalise_device(raw)
    if not fields["external_device_id"]:
        return

    existing = (
        await db.execute(
            select(ManagedDevice).where(
                ManagedDevice.tenant_id == integration.tenant_id,
                ManagedDevice.integration_id == integration.id,
                ManagedDevice.external_device_id == fields["external_device_id"],
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            ManagedDevice(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                external_device_id=fields["external_device_id"],
                device_name=fields["device_name"],
                operating_system=fields["operating_system"],
                os_version=fields["os_version"],
                compliance_state=fields["compliance_state"],
                enrolled_user_email=fields["enrolled_user_email"],
                last_sync_to_mdm_at=fields["last_sync_to_mdm_at"],
                last_synced_at=now,
            )
        )
    else:
        for key in (
            "device_name",
            "operating_system",
            "os_version",
            "compliance_state",
            "enrolled_user_email",
            "last_sync_to_mdm_at",
        ):
            setattr(existing, key, fields[key])
        existing.last_synced_at = now
