"""Slack OAuth v2 helpers — authorize URL + code exchange.

The flow:

  1. Admin clicks "Connect Slack workspace" on /dashboard/registry.
     Frontend opens `GET /api/v1/integrations/slack/install`.
  2. That endpoint redirects to Slack's authorize URL with a
     `state` parameter (HMAC-signed; carries tenant_id + user_id).
  3. Admin approves; Slack redirects back to
     `GET /api/v1/integrations/slack/callback?code=…&state=…`.
  4. Callback verifies state, exchanges code for an access token,
     stores the encrypted token in SlackWorkspace.

Scopes (matches the bot doc §5.3):
  chat:write           — post DMs
  im:write             — open DMs
  im:read              — read DM history (limited; webhook-driven)
  users:read           — list workspace users (for audience)
  users:read.email     — email→user_id resolution
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_TOKEN_URL = "https://slack.com/api/oauth.v2.access"

SLACK_SCOPES = [
    "chat:write",
    "im:write",
    "im:read",
    "users:read",
    "users:read.email",
]


class OAuthStateError(Exception):
    """State parameter doesn't verify — possible CSRF or stale link."""


class OAuthExchangeError(Exception):
    """Slack rejected the code exchange (expired / wrong secret / etc.)."""


# ─── State HMAC ──────────────────────────────────────────────────────


def encode_state(
    *, signing_secret: str, tenant_id: uuid.UUID, user_id: uuid.UUID, nonce: str
) -> str:
    """Build a signed state payload.

    Format: `<b64(json)>.<hex_hmac>` so the callback can verify
    `tenant_id`/`user_id` haven't been tampered with.
    """
    import base64

    payload = {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "nonce": nonce,
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    sig = hmac.new(
        signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{sig}"


def decode_state(*, signing_secret: str, state: str) -> dict[str, str]:
    """Verify + parse the state. Raises OAuthStateError on mismatch."""
    import base64

    if "." not in state:
        raise OAuthStateError("state must contain a '.' separator")
    encoded, sig = state.rsplit(".", 1)
    expected = hmac.new(
        signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise OAuthStateError("state HMAC mismatch")

    padding = "=" * (-len(encoded) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(encoded + padding)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise OAuthStateError(f"state payload malformed: {exc}") from exc

    for key in ("tenant_id", "user_id", "nonce"):
        if key not in payload:
            raise OAuthStateError(f"state missing field: {key}")
    return payload


# ─── Authorize URL ───────────────────────────────────────────────────


def build_authorize_url(
    *,
    client_id: str,
    redirect_url: str,
    state: str,
    scopes: list[str] | None = None,
) -> str:
    """Build the Slack OAuth v2 authorize URL with our scope set."""
    params = {
        "client_id": client_id,
        "scope": ",".join(scopes or SLACK_SCOPES),
        "redirect_uri": redirect_url,
        "state": state,
    }
    return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"


# ─── Code exchange ───────────────────────────────────────────────────


async def exchange_code(
    *,
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    redirect_url: str,
    code: str,
) -> dict[str, Any]:
    """Exchange the `code` for a bot access token.

    Returns the relevant fields needed to populate SlackWorkspace:
      team_id, team_name, bot_user_id, bot_access_token, scopes (list).

    Raises OAuthExchangeError if Slack's response isn't ok.
    """
    resp = await client.post(
        SLACK_OAUTH_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_url,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise OAuthExchangeError(f"slack oauth.v2.access not-ok: {data.get('error')!r}")

    bot = data.get("bot_user_id") or ""
    access_token = data.get("access_token") or ""
    team = data.get("team") or {}
    if not access_token or not bot or not team.get("id"):
        raise OAuthExchangeError("oauth response missing required fields")

    return {
        "team_id": team["id"],
        "team_name": team.get("name") or "",
        "bot_user_id": bot,
        "bot_access_token": access_token,
        "scopes": (data.get("scope") or "").split(","),
    }
