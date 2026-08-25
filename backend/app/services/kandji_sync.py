"""Kandji sync orchestrator — managed device roster.

Same shape as jamf_sync.py with one key simplification: Kandji uses
a single static API token (no bearer-exchange dance), so credential
handling is a thinner wrapper around the Fernet-encrypted blob.

Credentials live in Integration.access_token_encrypted as
`{"base_url": str, "api_token": str}`.

Writes ManagedDevice rows tagged with operating_system="macOS" /
"iOS" / etc. The Kandji-vs-Intune-vs-Jamf provenance is recoverable
via Integration.provider on the device's integration_id FK.
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
from app.models.managed_device import ManagedDevice
from app.services.crypto import decrypt_token
from app.services.kandji_api import (
    KandjiAPIError,
    KandjiAuthError,
    iter_blueprints,
    iter_devices,
    normalise_device,
)

logger = structlog.get_logger()


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
            Integration.provider == IntegrationProvider.KANDJI,
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
        except (KandjiAPIError, httpx.HTTPError, ValueError) as exc:
            integration.last_error = f"{type(exc).__name__}: {exc}"[:500]
            await db.commit()
            logger.warning(
                "kandji_sync_failed",
                integration_id=str(integration.id),
                error=str(exc),
            )
    return processed


def _load_credentials(integration: Integration) -> dict[str, str]:
    if not integration.access_token_encrypted:
        raise KandjiAuthError(
            401,
            f"integration {integration.id} has no Kandji credentials stored — connect first",
        )
    try:
        raw = decrypt_token(integration.access_token_encrypted)
        creds = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise KandjiAuthError(401, f"kandji credential decryption failed: {exc}")

    for key in ("base_url", "api_token"):
        if not creds.get(key):
            raise KandjiAuthError(401, f"kandji credentials missing '{key}'")
    return creds


async def _sync_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
) -> None:
    creds = _load_credentials(integration)
    now = datetime.now(UTC)

    device_count = 0
    async for raw in iter_devices(client, base_url=creds["base_url"], api_token=creds["api_token"]):
        try:
            fields = normalise_device(raw)
            if fields is None:
                continue
            await _upsert_device(db, integration, fields, now)
            device_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kandji_sync_skip_device",
                integration_id=str(integration.id),
                error=str(exc),
            )

    # Blueprints — count only for v0.2; full upsert lands when the
    # blueprint table exists.
    blueprint_count = 0
    async for _bp in iter_blueprints(
        client, base_url=creds["base_url"], api_token=creds["api_token"]
    ):
        blueprint_count += 1

    integration.last_synced_at = now
    integration.last_error = None
    await db.commit()
    logger.info(
        "kandji_sync_complete",
        integration_id=str(integration.id),
        devices=device_count,
        blueprints=blueprint_count,
    )


async def _upsert_device(
    db: AsyncSession,
    integration: Integration,
    fields: dict[str, Any],
    now: datetime,
) -> None:
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
        ):
            setattr(existing, key, fields[key])
        existing.last_sync_to_mdm_at = fields["last_sync_to_mdm_at"]
        existing.last_synced_at = now
