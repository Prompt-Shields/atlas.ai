"""Microsoft identity platform OAuth v2 helpers.

Single source of truth for all Microsoft integrations (Entra ID,
Intune, Purview, Defender for Cloud Apps). They share the same:

  - Authorize endpoint: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
  - Token endpoint:     https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
  - Multi-tenant app: `tenant=common` for the authorize URL; the
    issued token carries the actual Azure AD tenant id.

State carries our atlas tenant_id + user_id + the IntegrationProvider
the admin is connecting (so the callback knows which row to update).

Mirrors `slack_oauth.py` so the maintenance burden stays light —
when a new Microsoft provider is added, only the scope list moves.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx

from app.models.integration import IntegrationProvider

# Multi-tenant authorize endpoint; specific tenant id is returned in
# the access token.
MS_AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


class MicrosoftOAuthStateError(Exception):
    """State HMAC mismatch / payload malformed."""


class MicrosoftOAuthError(Exception):
    """Microsoft rejected the code exchange or returned malformed data."""


# ─── State ───────────────────────────────────────────────────────────


def encode_state(
    *,
    signing_secret: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: IntegrationProvider,
    nonce: str,
) -> str:
    """Build a signed state carrying atlas tenant_id + provider."""
    payload = {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "provider": provider.value,
        "nonce": nonce,
    }
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    sig = hmac.new(
        signing_secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{sig}"


def decode_state(*, signing_secret: str, state: str) -> dict[str, str]:
    if "." not in state:
        raise MicrosoftOAuthStateError("state missing '.' separator")
    encoded, sig = state.rsplit(".", 1)
    expected = hmac.new(
        signing_secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise MicrosoftOAuthStateError("state HMAC mismatch")
    padding = "=" * (-len(encoded) % 4)
    try:
        body = base64.urlsafe_b64decode(encoded + padding)
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise MicrosoftOAuthStateError(f"state payload malformed: {exc}") from exc
    for key in ("tenant_id", "user_id", "provider", "nonce"):
        if key not in payload:
            raise MicrosoftOAuthStateError(f"state missing field: {key}")
    return payload


# ─── Authorize URL ───────────────────────────────────────────────────


def build_authorize_url(
    *,
    client_id: str,
    redirect_url: str,
    scopes: list[str],
    state: str,
    nonce: str | None = None,
) -> str:
    """Microsoft v2.0 authorize URL with code flow + offline access.

    `nonce` (OIDC) is included only when provided — the SSO login flow uses it
    to bind the returned id_token to this request; integration-connect omits it.
    """
    params = {
        "client_id": client_id,
        "response_type": "code",
        "response_mode": "query",
        "redirect_uri": redirect_url,
        "scope": " ".join(scopes),
        "state": state,
        # Force consent screen so admins see new scopes when we add
        # a new Microsoft provider that needs more permissions.
        "prompt": "consent",
    }
    if nonce is not None:
        params["nonce"] = nonce
    return f"{MS_AUTHORIZE_URL}?{urlencode(params)}"


# ─── Code exchange ───────────────────────────────────────────────────


async def exchange_code(
    *,
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    redirect_url: str,
    code: str,
    scopes: list[str],
) -> dict[str, Any]:
    """Exchange the authorize-code for an access + refresh token.

    Returns:
      access_token, refresh_token, expires_in (seconds),
      scope (list[str]), id_token (raw JWT — caller may decode for
      `tid` = Azure AD tenant id).
    """
    resp = await client.post(
        MS_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_url,
            "grant_type": "authorization_code",
            "scope": " ".join(scopes),
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise MicrosoftOAuthError(
            f"microsoft token endpoint did not return access_token: "
            f"{data.get('error')!r} {data.get('error_description')!r}"
        )
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_in": int(data.get("expires_in", 3600)),
        "scope": (data.get("scope") or "").split(" "),
        "id_token": data.get("id_token"),
    }


async def refresh_access_token(
    *,
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: list[str],
) -> dict[str, Any]:
    """Exchange a refresh_token for a fresh access_token.

    Microsoft rotates refresh tokens on every refresh — callers
    must persist the *new* refresh_token, not reuse the old one.
    """
    resp = await client.post(
        MS_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": " ".join(scopes),
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise MicrosoftOAuthError(f"refresh_token rejected: {data.get('error')!r}")
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_in": int(data.get("expires_in", 3600)),
        "scope": (data.get("scope") or "").split(" "),
    }


# ─── Azure AD tenant id from the id_token ────────────────────────────


def parse_tenant_id_from_id_token(id_token: str) -> str | None:
    """Decode the id_token (unverified) and return the `tid` claim.

    We don't verify the JWT signature here — the access token was
    issued by Microsoft over HTTPS to our client, so the `tid` claim
    is trusted. We use it only as a display/identifier value, not as
    an authn assertion.
    """
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        body = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(body.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    return claims.get("tid")
