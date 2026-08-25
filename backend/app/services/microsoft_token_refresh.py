"""Shared Microsoft OAuth token refresh for Integration sync workers.

Extracted from microsoft_sync.py (Entra) — also used by Intune,
Purview, and Defender CASB workers. Mid-batch token refresh logic is
identical across providers since they all share the v2.0 token
endpoint; the only per-provider variation is the scope list, which
we read from `Integration.scopes`.

Microsoft rotates refresh tokens on every refresh. The new
`refresh_token` is persisted (Fernet-encrypted) before continuing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.integration import Integration
from app.services.crypto import decrypt_token, encrypt_token
from app.services.microsoft_oauth import (
    MicrosoftOAuthError,
    refresh_access_token,
)

# How early to refresh — Microsoft access tokens last ~1h; refresh
# with 5min buffer so a long-running sync doesn't trip mid-way.
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


async def get_valid_access_token(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
) -> str:
    """Return a valid access token, refreshing if close to expiry.

    Mutates the `Integration` row in-place with the new access /
    refresh tokens (Fernet-encrypted) and commits. Caller owns the
    surrounding transaction context but the commit is local here so
    a partial sync failure doesn't lose the refreshed token.
    """
    if not integration.access_token_encrypted:
        raise MicrosoftOAuthError(
            f"integration {integration.id} has no access_token; re-OAuth required"
        )

    expires_at = integration.access_token_expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at - datetime.now(UTC) > TOKEN_REFRESH_BUFFER:
            return decrypt_token(integration.access_token_encrypted)

    if not integration.refresh_token_encrypted:
        raise MicrosoftOAuthError(
            f"integration {integration.id} access_token expired and no refresh_token"
        )

    refresh = decrypt_token(integration.refresh_token_encrypted)
    settings = get_settings()
    scopes = (integration.scopes or "").split(" ") if integration.scopes else []

    fresh = await refresh_access_token(
        client=client,
        client_id=settings.microsoft_client_id,
        client_secret=settings.microsoft_client_secret,
        refresh_token=refresh,
        scopes=scopes,
    )

    integration.access_token_encrypted = encrypt_token(fresh["access_token"])
    if fresh.get("refresh_token") and fresh["refresh_token"] != refresh:
        integration.refresh_token_encrypted = encrypt_token(fresh["refresh_token"])
    integration.access_token_expires_at = datetime.now(UTC) + timedelta(
        seconds=int(fresh["expires_in"])
    )
    if fresh.get("scope"):
        integration.scopes = " ".join(fresh["scope"])
    await db.commit()

    return fresh["access_token"]
