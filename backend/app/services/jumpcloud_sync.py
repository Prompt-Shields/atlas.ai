"""JumpCloud sync orchestrator — managed system roster.

Final of the three v0.2 MDM adapters (Jamf #46, Kandji #47).
Pulls systems (cross-platform: macOS / Windows / Linux) via the
JumpCloud Directory API.

Credentials live in Integration.access_token_encrypted as
`{"api_key": str}`. JumpCloud's API key is per-tenant and grants
access to the entire tenant's systems + users + groups.
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
from app.services.jumpcloud_api import (
    JumpCloudAPIError,
    JumpCloudAuthError,
    iter_system_groups,
    iter_systems,
    normalise_system,
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
            Integration.provider == IntegrationProvider.JUMPCLOUD,
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
        except (JumpCloudAPIError, httpx.HTTPError, ValueError) as exc:
            integration.last_error = f"{type(exc).__name__}: {exc}"[:500]
            await db.commit()
            logger.warning(
                "jumpcloud_sync_failed",
                integration_id=str(integration.id),
                error=str(exc),
            )
    return processed


def _load_credentials(integration: Integration) -> dict[str, str]:
    if not integration.access_token_encrypted:
        raise JumpCloudAuthError(
            401,
            f"integration {integration.id} has no JumpCloud credentials — connect first",
        )
    try:
        raw = decrypt_token(integration.access_token_encrypted)
        creds = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise JumpCloudAuthError(401, f"jumpcloud credential decryption failed: {exc}")
    if not creds.get("api_key"):
        raise JumpCloudAuthError(401, "jumpcloud credentials missing 'api_key'")
    return creds


async def _sync_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
) -> None:
    creds = _load_credentials(integration)
    now = datetime.now(UTC)

    system_count = 0
    async for raw in iter_systems(client, api_key=creds["api_key"]):
        try:
            fields = normalise_system(raw)
            if fields is None:
                continue
            await _upsert_system(db, integration, fields, now)
            system_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "jumpcloud_sync_skip_system",
                integration_id=str(integration.id),
                error=str(exc),
            )

    group_count = 0
    async for _g in iter_system_groups(client, api_key=creds["api_key"]):
        group_count += 1

    integration.last_synced_at = now
    integration.last_error = None
    await db.commit()
    logger.info(
        "jumpcloud_sync_complete",
        integration_id=str(integration.id),
        systems=system_count,
        groups=group_count,
    )


async def _upsert_system(
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
