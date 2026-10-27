"""Entra ID SSO login (PRO-55).

Authentication path for "Sign in with Microsoft". Two concerns:

1. **ID-token verification** — the token comes from the back-channel code
   exchange (`microsoft_oauth.exchange_code`). Before any claim is read, its
   RS256 signature is verified against Microsoft's published v2.0 signing keys
   (`JWKS_URL`), the key chosen by the token's `kid`. Only RS256 is accepted.
   The keys are cached for as long as Microsoft's `Cache-Control` allows; an
   unknown `kid` fetches them early at most once a minute, in case Microsoft
   rotated a key in. A bad signature, an unknown key or a failed fetch rejects
   the login. The claims (issuer, audience, nonce, expiry) are then validated
   as before.

2. **Provision-or-link (LINK-ONLY for v1)** — because the app is a multi-tenant
   Entra app (`tenant=common`), auto-creating accounts would let any Microsoft
   account self-provision. So we only ever *link* an SSO identity to a user that
   already exists in the platform (seeded, invited, or directory-synced — see
   PRO-56). Match by `entra_object_id`, then by `email` (backfilling the oid).
   Unknown or inactive users are rejected. JIT auto-provisioning + tenant
   mapping is deliberately deferred.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JOSEError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User

_MS_ISS_PREFIX = "https://login.microsoftonline.com/"
_MS_ISS_SUFFIX = "/v2.0"


class SSOIdTokenError(Exception):
    """The id-token's signature or claims failed verification."""


class SSOLoginStateError(Exception):
    """The login `state` HMAC did not verify / payload malformed."""


class SSOAccountNotProvisionedError(Exception):
    """No active platform user is linked to this Entra identity."""


# ─── ID-token signature ──────────────────────────────────────────────

# Microsoft's v2.0 signing keys. The app is multi-tenant (`tenant=common`), and
# the common document lists the keys every tenant's tokens are signed with.
JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
_DEFAULT_MAX_AGE = 3600
# A token naming a key Atlas has not seen makes it fetch Microsoft's keys early,
# in case Microsoft rotated one in. Such tokens can come from anyone, so that
# early fetch happens at most once in this many seconds.
FORCED_REFRESH_INTERVAL = 60.0
_MAX_AGE_RE = re.compile(r"max-age=(\d+)")

KeyFetcher = Callable[[], Awaitable[tuple[dict[str, dict[str, Any]], int]]]


def _signing_keys(document: Any) -> dict[str, dict[str, Any]]:
    """The RSA signing keys of a JWKS document, by `kid`."""
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("Unexpected key document")
    keys: dict[str, dict[str, Any]] = {}
    for key in document["keys"]:
        if not isinstance(key, dict) or not isinstance(key.get("kid"), str):
            continue
        if key.get("kty") != "RSA" or key.get("use", "sig") != "sig":
            continue
        keys[key["kid"]] = key
    return keys


async def fetch_microsoft_keys(
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, dict[str, Any]], int]:
    """Microsoft's current signing keys, and for how long they may be kept."""
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        response = await client.get(JWKS_URL)
        response.raise_for_status()
    match = _MAX_AGE_RE.search(response.headers.get("cache-control", ""))
    max_age = int(match.group(1)) if match else _DEFAULT_MAX_AGE
    return _signing_keys(response.json()), max_age


class EntraKeySet:
    """Verifies id-token signatures, caching Microsoft's keys for as long as
    Microsoft says they may be kept."""

    def __init__(
        self,
        fetch: KeyFetcher = fetch_microsoft_keys,
        clock: Callable[[], float] = time.time,
        forced_refresh_interval: float = FORCED_REFRESH_INTERVAL,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._forced_refresh_interval = forced_refresh_interval
        self._keys: dict[str, dict[str, Any]] = {}
        self._expires_at = 0.0
        self._last_forced_at = -math.inf
        # One fetch at a time, so requests that arrive while one is under way wait
        # for it and read its result. An asyncio lock belongs to one event loop, so
        # a new loop (each test has its own) gets a new lock.
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _fetch_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _cached(self, kid: str) -> dict[str, Any] | None:
        if self._clock() >= self._expires_at:
            return None
        return self._keys.get(kid)

    async def _key(self, kid: str) -> dict[str, Any]:
        key = self._cached(kid)
        if key is not None:
            return key
        async with self._fetch_lock():
            # Another request may have fetched while this one waited.
            key = self._cached(kid)
            if key is not None:
                return key
            now = self._clock()
            if now < self._expires_at:
                # The keys are current but lack this one: fetch early only if no
                # early fetch was made in the last interval.
                if now - self._last_forced_at < self._forced_refresh_interval:
                    raise SSOIdTokenError("unknown signing key")
                self._last_forced_at = now
            try:
                keys, max_age = await self._fetch()
            except (httpx.HTTPError, ValueError) as exc:
                raise SSOIdTokenError("signing keys unavailable") from exc
            self._keys = keys
            self._expires_at = self._clock() + max_age
        key = self._keys.get(kid)
        if key is None:
            raise SSOIdTokenError("unknown signing key")
        return key

    async def verify_signature(self, id_token: str) -> None:
        """Raise unless the token is RS256-signed by one of Microsoft's keys.

        Checks the signature only; the claims are validated by `validate_claims`.
        """
        try:
            header = jwt.get_unverified_header(id_token)
        except JOSEError as exc:
            raise SSOIdTokenError("id_token is not a JWT") from exc
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise SSOIdTokenError("id_token is not RS256-signed with a key id")
        key = await self._key(header["kid"])
        try:
            jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                options={
                    "verify_signature": True,
                    "verify_aud": False,
                    "verify_iat": False,
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iss": False,
                    "verify_sub": False,
                    "verify_jti": False,
                    "verify_at_hash": False,
                },
            )
        except JOSEError as exc:
            raise SSOIdTokenError("id_token signature did not verify") from exc


_key_set = EntraKeySet()


def get_entra_key_set() -> EntraKeySet:
    """The process-wide key set; tests replace this function to use local keys."""
    return _key_set


# ─── ID-token claims ─────────────────────────────────────────────────


def decode_id_token_claims(id_token: str) -> dict:
    """Base64url-decode the JWT payload segment into a claims dict.

    Reads the claims only: `complete_sso_login` verifies the signature first.
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
    key_set: EntraKeySet | None = None,
) -> dict:
    """Exchange the auth code, verify the id-token, link the user, issue tokens.

    Caller (the callback endpoint) owns the httpx client and the DB commit.
    """
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

    await (key_set or get_entra_key_set()).verify_signature(id_token)
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
