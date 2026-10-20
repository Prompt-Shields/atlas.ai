"""Shared plumbing for the dedicated per-provider connect endpoints.

Both ``routers/mdm_connect.py`` (Jamf / Kandji / JumpCloud) and
``routers/cost_connect.py`` (Anthropic / OpenAI / Cursor / Copilot /
Vercel) follow the same four-step shape:

  1. Accept a typed credential body.
  2. Live-verify the credentials against the vendor before persisting,
     so a typo fails at the form rather than silently at 03:00 in the
     sync worker.
  3. Fernet-encrypt the secret and upsert the ``Integration`` row.
  4. Return the ``IntegrationCard`` so the grid re-renders connected.

Steps 3 and 4 are identical across every provider, so they live here.
Step 2 is genuinely provider-specific and stays in the routers.

``redact_config`` is the one piece of policy worth stating out loud:
``Integration.config_json`` is returned to the browser by
``GET /integrations`` (as ``IntegrationCard.config``), so anything
secret must never be stored there in cleartext. Providers that need a
second credential store it encrypted under a ``*_encrypted`` suffix,
and this module strips those keys on the way out.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.schemas.integration import IntegrationCard

#: Suffix marking a ``config_json`` value as Fernet ciphertext. Keys
#: ending in this are stripped before the config reaches the browser.
ENCRYPTED_SUFFIX = "_encrypted"


def redact_config(raw: str | None) -> dict[str, Any]:
    """Decode ``config_json`` → dict, dropping encrypted-secret keys.

    Malformed JSON yields ``{}`` rather than raising — a junk config
    should not 500 the integrations grid.
    """
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        # TypeError guards a non-str slipping past the type hint, which
        # the caller this replaced also tolerated.
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: v for k, v in parsed.items() if not k.endswith(ENCRYPTED_SUFFIX)}


async def upsert_connected(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: IntegrationProvider,
    display_name: str,
    external_id: str | None,
    encrypted_blob: str,
    config: dict[str, Any] | None = None,
) -> Integration:
    """Create or refresh this tenant's integration row for ``provider``.

    One row per (tenant, provider): reconnecting with a rotated key
    overwrites the stored secret and clears ``last_error`` rather than
    accumulating duplicate rows.
    """
    existing = (
        await db.execute(
            select(Integration).where(
                Integration.tenant_id == tenant_id,
                Integration.provider == provider,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    config_json = json.dumps(config) if config is not None else None

    if existing is None:
        record = Integration(
            tenant_id=tenant_id,
            provider=provider,
            display_name=display_name,
            external_id=external_id,
            external_name=display_name,
            access_token_encrypted=encrypted_blob,
            config_json=config_json,
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
    if config_json is not None:
        existing.config_json = config_json
    existing.status = IntegrationStatus.CONNECTED
    existing.is_active = True
    existing.connected_by_user_id = user_id
    existing.connected_at = now
    existing.last_error = None
    await db.commit()
    await db.refresh(existing)
    return existing


def to_card(integration: Integration) -> IntegrationCard:
    """Render a freshly connected row as the grid's card payload."""
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
        config=redact_config(integration.config_json),
    )
