"""Entra ID SSO login (PRO-55).

Authentication path for "Sign in with Microsoft". Two concerns:

1. **ID-token claims** — the token is obtained over the back-channel TLS code
   exchange (`microsoft_oauth.exchange_code`), so — consistent with the trust
   model already used in `microsoft_oauth.parse_tenant_id_from_id_token` — we
   trust the channel for integrity and validate the *claims* (issuer, audience,
   nonce, expiry) rather than re-verifying the RS256 signature.
   HARDENING FOLLOW-UP: add JWKS-based signature verification against
   `https://login.microsoftonline.com/common/discovery/v2.0/keys`.

2. **Provision-or-link (LINK-ONLY for v1)** — because the app is a multi-tenant
   Entra app (`tenant=common`), auto-creating accounts would let any Microsoft
   account self-provision. So we only ever *link* an SSO identity to a user that
   already exists in the platform (seeded, invited, or directory-synced — see
   PRO-56). Match by `entra_object_id`, then by `email` (backfilling the oid).
   Unknown or inactive users are rejected. JIT auto-provisioning + tenant
   mapping is deliberately deferred.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User

_MS_ISS_PREFIX = "https://login.microsoftonline.com/"
_MS_ISS_SUFFIX = "/v2.0"


class SSOIdTokenError(Exception):
    """The id-token claims failed validation."""


class SSOLoginStateError(Exception):
    """The login `state` HMAC did not verify / payload malformed."""


class SSOAccountNotProvisionedError(Exception):
    """No active platform user is linked to this Entra identity."""


# ─── ID-token claims ─────────────────────────────────────────────────


def decode_id_token_claims(id_token: str) -> dict:
    """Base64url-decode the JWT payload segment into a claims dict.

    Does not verify the signature — see module docstring trust model.
    """
    parts = id_token.split(".")
    if len(parts) < 2:
        raise SSOIdTokenError("id_token is not a JWT")
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        body = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(body.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SSOIdTokenError(f"id_token payload malformed: {exc}") from exc


def validate_claims(
    claims: dict,
    *,
    expected_audience: str,
    expected_nonce: str,
    now_ts: int,
) -> None:
    """Validate issuer, audience, nonce and expiry. Raises on any failure."""
    iss = claims.get("iss", "")
    if not (iss.startswith(_MS_ISS_PREFIX) and iss.endswith(_MS_ISS_SUFFIX)):
        raise SSOIdTokenError(f"unexpected issuer: {iss!r}")
    if claims.get("aud") != expected_audience:
        raise SSOIdTokenError("audience mismatch")
    if claims.get("nonce") != expected_nonce:
        raise SSOIdTokenError("nonce mismatch")
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < now_ts:
        raise SSOIdTokenError("id_token expired or missing exp")


def email_from_claims(claims: dict) -> str | None:
    """Entra puts the email in `email`, else `preferred_username`/`upn`."""
    for key in ("email", "preferred_username", "upn"):
        value = claims.get(key)
        if value:
            return str(value).lower()
    return None


# ─── Login state (stateless nonce binding) ───────────────────────────


def encode_login_state(*, signing_secret: str, nonce: str) -> str:
    """Signed state carrying the OIDC nonce; bound via HMAC (no server store)."""
    body = json.dumps({"nonce": nonce}, sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    sig = hmac.new(
        signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{sig}"


def decode_login_state(*, signing_secret: str, state: str) -> dict[str, str]:
    if "." not in state:
        raise SSOLoginStateError("state missing '.' separator")
    encoded, sig = state.rsplit(".", 1)
    expected = hmac.new(
        signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise SSOLoginStateError("state HMAC mismatch")
    padding = "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SSOLoginStateError(f"state payload malformed: {exc}") from exc
    if "nonce" not in payload:
        raise SSOLoginStateError("state missing nonce")
    return payload


# ─── Provision-or-link (link-only) ───────────────────────────────────


async def resolve_sso_user(db: AsyncSession, *, entra_object_id: str, email: str) -> User:
    """Return the active platform user linked to this Entra identity.

    LINK-ONLY: match by entra_object_id, then by email (backfilling the oid).
    Never auto-creates. Raises SSOAccountNotProvisionedError otherwise.
    """
    by_oid = (
        await db.execute(
            select(User)
            .where(User.entra_object_id == entra_object_id)
            .options(selectinload(User.roles))
        )
    ).scalar_one_or_none()
    if by_oid is not None:
        if not by_oid.is_active:
            raise SSOAccountNotProvisionedError("linked account is inactive")
        return by_oid

    by_email = (
        await db.execute(
            select(User).where(User.email == email.lower()).options(selectinload(User.roles))
        )
    ).scalar_one_or_none()
    if by_email is None or not by_email.is_active:
        raise SSOAccountNotProvisionedError(
            "no active platform account for this Microsoft identity"
        )

    # First SSO login for an existing local user — link by backfilling the oid.
    by_email.entra_object_id = entra_object_id
    await db.flush()
    return by_email


# ─── End-to-end orchestration ────────────────────────────────────────

# Minimal OIDC scopes for authentication (no Graph data scopes).
LOGIN_SCOPES = ["openid", "profile", "email"]


async def complete_sso_login(
    db: AsyncSession,
    *,
    code: str,
    state: str,
    settings,
    client,
    now_ts: int | None = None,
) -> dict:
    """Exchange the auth code, validate the id-token, link the user, issue tokens.

    Caller (the callback endpoint) owns the httpx client and the DB commit.
    """
    import time

    from app.services import auth_service
    from app.services.microsoft_oauth import exchange_code

    payload = decode_login_state(signing_secret=settings.microsoft_signing_secret, state=state)
    nonce = payload["nonce"]

    tokens = await exchange_code(
        client=client,
        client_id=settings.microsoft_client_id,
        client_secret=settings.microsoft_client_secret,
        redirect_url=settings.microsoft_sso_redirect_url,
        code=code,
        scopes=LOGIN_SCOPES,
    )
    id_token = tokens.get("id_token")
    if not id_token:
        raise SSOIdTokenError("token response did not include an id_token")

    claims = decode_id_token_claims(id_token)
    validate_claims(
        claims,
        expected_audience=settings.microsoft_client_id,
        expected_nonce=nonce,
        now_ts=now_ts if now_ts is not None else int(time.time()),
    )

    oid = claims.get("oid")
    email = email_from_claims(claims)
    if not oid or not email:
        raise SSOIdTokenError("id_token missing oid/email claims")

    try:
        user = await resolve_sso_user(db, entra_object_id=oid, email=email)
    except SSOAccountNotProvisionedError:
        # LINK-ONLY by default. Self-serve sign-up (SP-Azure) turns an unlinked
        # Microsoft identity into a JIT tenant provision / member join, keyed on
        # the Azure directory (`tid`). Reaching here means no user matched by
        # oid or email, so the existing-email path already handled dedupe/link.
        if not getattr(settings, "sso_self_serve_signup", False):
            raise
        tid = claims.get("tid")
        if not tid:
            raise SSOIdTokenError("id_token missing tid claim for self-serve")

        from app.services.signup_service import provision_or_join_sso_user

        user = await provision_or_join_sso_user(
            db,
            azure_tenant_id=tid,
            email=email,
            entra_object_id=oid,
            full_name=claims.get("name", ""),
        )
    return await auth_service.issue_tokens(db, user)
