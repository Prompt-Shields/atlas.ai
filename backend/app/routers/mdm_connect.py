"""Dedicated MDM connect endpoints — Jamf / Kandji / JumpCloud.

Replaces the v0.2 plaintext-via-PATCH credential entry with a
purpose-built per-MDM connect surface. The endpoint:

  1. Accepts the MDM's credential set (Jamf user/pass, Kandji api
     token, JumpCloud api key) in a typed Pydantic body.
  2. Performs a live one-shot auth against the MDM API to verify
     the credentials work before persisting.
  3. Fernet-encrypts the credential blob server-side and stores it
     in Integration.access_token_encrypted. Plaintext password
     never lives on disk; only the cleartext request body, which
     never gets logged.
  4. Upserts the Integration row (creates if missing) with
     status=CONNECTED.
  5. Returns the IntegrationCard so the frontend renders the
     refreshed state immediately.

Why three endpoints rather than one polymorphic:
  Each MDM's credential shape is genuinely different — Jamf needs
  3 fields (server_url, username, password), Kandji 2 (base_url,
  api_token), JumpCloud 1 (api_key). Forcing them through one
  endpoint with optional fields loses input-validation safety.

Same per-tenant scoping as PATCH /integrations/{id} — OrgAdmin can
connect; SuperAdmin can connect for their own tenant only (does not
cross-tenant by design).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin
from app.database import get_db_session
from app.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.schemas.integration import IntegrationCard
from app.services.crypto import encrypt_token
from app.services.jamf_api import (
    JamfAPIError,
    JamfAuthError,
    acquire_bearer_token,
    invalidate_bearer_token,
)
from app.services.jumpcloud_api import (
    JumpCloudAPIError,
    JumpCloudAuthError,
    jumpcloud_get,
)
from app.services.kandji_api import (
    KandjiAPIError,
    KandjiAuthError,
    kandji_get,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/integrations", tags=["Integrations"])


# ─── Request schemas ─────────────────────────────────────────────────


class JamfConnectRequest(BaseModel):
    server_url: HttpUrl = Field(
        ...,
        description="Full Jamf Pro URL, e.g. https://yourorg.jamfcloud.com",
    )
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1)


class KandjiConnectRequest(BaseModel):
    base_url: HttpUrl = Field(
        ...,
        description=("Kandji tenant base URL, e.g. https://yourorg.api.kandji.io"),
    )
    api_token: str = Field(..., min_length=10)


class JumpCloudConnectRequest(BaseModel):
    api_key: str = Field(
        ...,
        min_length=10,
        description="JumpCloud API key from Settings → API Settings",
    )


# ─── Shared helpers ──────────────────────────────────────────────────


async def _upsert_connected(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: IntegrationProvider,
    display_name: str,
    external_id: str | None,
    encrypted_blob: str,
) -> Integration:
    existing = (
        await db.execute(
            select(Integration).where(
                Integration.tenant_id == tenant_id,
                Integration.provider == provider,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)

    if existing is None:
        record = Integration(
            tenant_id=tenant_id,
            provider=provider,
            display_name=display_name,
            external_id=external_id,
            external_name=display_name,
            access_token_encrypted=encrypted_blob,
            scopes=None,
            status=IntegrationStatus.CONNECTED,
            is_active=True,
            connected_by_user_id=user_id,
            connected_at=now,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record

    existing.access_token_encrypted = encrypted_blob
    existing.external_id = external_id or existing.external_id
    existing.display_name = display_name or existing.display_name
    existing.status = IntegrationStatus.CONNECTED
    existing.is_active = True
    existing.connected_by_user_id = user_id
    existing.connected_at = now
    existing.last_error = None
    await db.commit()
    await db.refresh(existing)
    return existing


def _to_card(integration: Integration) -> IntegrationCard:
    from app.services.integration_registry import get_provider

    meta = get_provider(integration.provider)
    return IntegrationCard(
        meta={
            "provider": meta.provider,
            "display_name": meta.display_name,
            "short_name": meta.short_name,
            "category": meta.category,
            "vendor": meta.vendor,
            "logo_slug": meta.logo_slug,
            "description": meta.description,
            "capabilities": meta.capabilities,
            "available": meta.available,
            "onboarding_recommended": meta.onboarding_recommended,
        },
        status=integration.status,
        integration_id=str(integration.id),
        display_name=integration.display_name,
        external_id=integration.external_id,
        external_name=integration.external_name,
        scopes=[],
        last_synced_at=integration.last_synced_at,
        last_error=integration.last_error,
        connected_at=integration.connected_at,
        config={},
    )


# ─── Jamf Pro ────────────────────────────────────────────────────────


@router.post("/jamf/connect", response_model=IntegrationCard)
async def jamf_connect(
    payload: JamfConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Verify Jamf credentials by acquiring a one-shot bearer, then
    encrypt + persist them. Returns the IntegrationCard.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    server_url = str(payload.server_url).rstrip("/")

    # Live auth check.
    async with httpx.AsyncClient() as client:
        try:
            bearer = await acquire_bearer_token(
                client,
                base_url=server_url,
                username=payload.username,
                password=payload.password,
            )
        except JamfAuthError as exc:
            raise UnauthorizedError(f"Jamf rejected credentials: {exc}")
        except JamfAPIError as exc:
            raise ConflictError(f"Jamf API error: {exc}")
        except httpx.HTTPError as exc:
            raise ConflictError(f"Could not reach Jamf server: {exc}")
        # Best-effort revoke so we don't leave a 30-min bearer dangling.
        try:
            await invalidate_bearer_token(client, base_url=server_url, token=bearer["token"])
        except Exception:  # noqa: BLE001
            pass

    # Encrypt + persist.
    blob = encrypt_token(
        json.dumps(
            {
                "server_url": server_url,
                "username": payload.username,
                "password": payload.password,
            }
        )
    )
    record = await _upsert_connected(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        provider=IntegrationProvider.JAMF_PRO,
        display_name=f"Jamf Pro ({server_url})",
        external_id=server_url,
        encrypted_blob=blob,
    )
    logger.info(
        "jamf_connected",
        tenant_id=str(user.tenant_id),
        integration_id=str(record.id),
    )
    return _to_card(record)


# ─── Kandji ──────────────────────────────────────────────────────────


@router.post("/kandji/connect", response_model=IntegrationCard)
async def kandji_connect(
    payload: KandjiConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Verify Kandji API token by listing the first page of devices."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    base_url = str(payload.base_url).rstrip("/")

    async with httpx.AsyncClient() as client:
        try:
            # One-shot verification — small page, just confirms auth.
            await kandji_get(
                client,
                base_url=base_url,
                api_token=payload.api_token,
                path="/api/v1/devices",
                params={"limit": 1, "offset": 0},
            )
        except KandjiAuthError as exc:
            raise UnauthorizedError(f"Kandji rejected API token: {exc}")
        except KandjiAPIError as exc:
            raise ConflictError(f"Kandji API error: {exc}")
        except httpx.HTTPError as exc:
            raise ConflictError(f"Could not reach Kandji: {exc}")

    blob = encrypt_token(json.dumps({"base_url": base_url, "api_token": payload.api_token}))
    record = await _upsert_connected(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        provider=IntegrationProvider.KANDJI,
        display_name=f"Kandji ({base_url})",
        external_id=base_url,
        encrypted_blob=blob,
    )
    logger.info(
        "kandji_connected",
        tenant_id=str(user.tenant_id),
        integration_id=str(record.id),
    )
    return _to_card(record)


# ─── JumpCloud ───────────────────────────────────────────────────────


@router.post("/jumpcloud/connect", response_model=IntegrationCard)
async def jumpcloud_connect(
    payload: JumpCloudConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Verify JumpCloud API key by reading the systems list."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    async with httpx.AsyncClient() as client:
        try:
            await jumpcloud_get(
                client,
                api_key=payload.api_key,
                path="/api/systems",
                params={"limit": 1, "skip": 0},
            )
        except JumpCloudAuthError as exc:
            raise UnauthorizedError(f"JumpCloud rejected API key: {exc}")
        except JumpCloudAPIError as exc:
            raise ConflictError(f"JumpCloud API error: {exc}")
        except httpx.HTTPError as exc:
            raise ConflictError(f"Could not reach JumpCloud: {exc}")

    blob = encrypt_token(json.dumps({"api_key": payload.api_key}))
    record = await _upsert_connected(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        provider=IntegrationProvider.JUMPCLOUD,
        display_name="JumpCloud",
        external_id=None,
        encrypted_blob=blob,
    )
    logger.info(
        "jumpcloud_connected",
        tenant_id=str(user.tenant_id),
        integration_id=str(record.id),
    )
    return _to_card(record)
