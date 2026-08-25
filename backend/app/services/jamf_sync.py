"""Jamf Pro sync orchestrator — managed Mac roster + computer groups.

Mirrors the per-integration loop pattern from intune_sync.py /
microsoft_sync.py, with the key difference that Jamf uses
Basic-auth-for-bearer-token rather than Microsoft OAuth.

Credential storage (v0.2)
  Jamf credentials live in Integration.access_token_encrypted as a
  Fernet-encrypted JSON blob `{"server_url": str, "username": str,
  "password": str}`. We never store the bearer token long-term —
  acquire fresh per sync run, invalidate on the way out.

  The v0.3 connect flow (TODO — small UI PR) lets an admin enter
  the credentials via /dashboard/integrations/JAMF_PRO with the
  encryption applied server-side.

Outputs
  ManagedDevice is reused as the destination table — v0.4 migrates
  it to a generic `ManagedDevice` table. Today an ManagedDevice row
  carries `operating_system="macOS"` for Jamf-sourced devices,
  distinguishable from Intune via the source integration_id.
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
from app.services.jamf_api import (
    JamfAPIError,
    JamfAuthError,
    acquire_bearer_token,
    invalidate_bearer_token,
    iter_computers,
    iter_groups,
    normalise_computer,
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
            Integration.provider == IntegrationProvider.JAMF_PRO,
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
            JamfAPIError,
            httpx.HTTPError,
            ValueError,
        ) as exc:
            integration.last_error = f"{type(exc).__name__}: {exc}"[:500]
            await db.commit()
            logger.warning(
                "jamf_sync_failed",
                integration_id=str(integration.id),
                error=str(exc),
            )
    return processed


# ─── Per-integration sync ────────────────────────────────────────────


def _load_credentials(integration: Integration) -> dict[str, str]:
    """Decrypt the Jamf credential blob from Integration.access_token_encrypted.

    Returns: {"server_url": ..., "username": ..., "password": ...}
    Raises JamfAuthError if missing / malformed.
    """
    if not integration.access_token_encrypted:
        raise JamfAuthError(
            401,
            f"integration {integration.id} has no Jamf credentials stored — connect first",
        )
    try:
        raw = decrypt_token(integration.access_token_encrypted)
        creds = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise JamfAuthError(401, f"jamf credential decryption failed: {exc}")

    for key in ("server_url", "username", "password"):
        if not creds.get(key):
            raise JamfAuthError(401, f"jamf credentials missing '{key}'")
    return creds


async def _sync_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
) -> None:
    creds = _load_credentials(integration)
    bearer = await acquire_bearer_token(
        client,
        base_url=creds["server_url"],
        username=creds["username"],
        password=creds["password"],
    )
    token = bearer["token"]
    now = datetime.now(UTC)

    try:
        # Computers (Macs).
        seen_ids: set[str] = set()
        device_count = 0
        async for raw in iter_computers(client, base_url=creds["server_url"], token=token):
            try:
                fields = normalise_computer(raw)
                if fields is None:
                    continue
                await _upsert_computer(db, integration, fields, now)
                seen_ids.add(fields["external_device_id"])
                device_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "jamf_sync_skip_computer",
                    integration_id=str(integration.id),
                    error=str(exc),
                )

        deactivated = await _deactivate_missing(db, integration, seen_ids, now)

        # Groups (informational — written to DirectoryGroup-like row
        # in a v0.3 follow-up; v0.2 just logs the count).
        group_count = 0
        async for _raw in iter_groups(client, base_url=creds["server_url"], token=token):
            group_count += 1

        integration.last_synced_at = now
        integration.last_error = None
        await db.commit()

        logger.info(
            "jamf_sync_complete",
            integration_id=str(integration.id),
            devices=device_count,
            deactivated_devices=deactivated,
            groups=group_count,
        )
    finally:
        # Best-effort revoke so we don't leave a 30-min token dangling.
        await invalidate_bearer_token(client, base_url=creds["server_url"], token=token)


async def _upsert_computer(
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


async def _deactivate_missing(
    db: AsyncSession,
    integration: Integration,
    seen_ids: set[str],
    now: datetime,
) -> int:
    """Jamf devices not seen in this run are kept — Jamf inventory
    is the source of truth; missing IDs may just mean the device was
    archived or moved to a different scope.

    Returns 0 always; we never deactivate Jamf devices. Reserved for
    future symmetry with the Intune sync if customer feedback asks
    for it.
    """
    return 0


# Suppress unused-arg lint for v0.2.
_ = _deactivate_missing
