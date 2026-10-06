"""Firebase ID tokens, as Promptly Guide presents them (promptly-guide #37).

Guide signs people in through the organisation's identity provider by way of a
Google Firebase Authentication (Identity Platform) project. Atlas does not issue
those tokens, so it verifies them the way Firebase documents:

  * RS256, signed by one of Google's `securetoken` keys (the `kid` must be one of
    the certificates published at `CERTS_URL`);
  * `iss` is `https://securetoken.google.com/<project>` and `aud` is `<project>`;
  * not expired, not issued in the future, a non-empty `sub`.

Which project is acceptable is not a setting here: it is whichever project a
tenant connected (`GuideConnection`), so the caller looks the connection up by the
token's unverified `aud` and then verifies against exactly that project.

What a verified token is used for, and nothing more: the project and the Identity
Platform tenant (to find the Atlas tenant), `sub` (to stop one person contributing
twice in a month, see `guide_adoption_service.receipt`), and -- for `GET
/guide/groups` only -- the email, to find the person's SCIM groups (#58). The name
and the token's own group claims are never read, and nothing from the token is
logged or stored.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from jose import JWTError, jwt

CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
)
ISSUER_PREFIX = "https://securetoken.google.com/"
# A clock a little ahead of Google's should not reject a token minted a moment ago.
_LEEWAY_SECONDS = 60
_DEFAULT_MAX_AGE = 3600
_MAX_AGE_RE = re.compile(r"max-age=(\d+)")

CertFetcher = Callable[[], Awaitable[tuple[dict[str, str], int]]]


class FirebaseTokenError(Exception):
    """The token is not a valid Firebase ID token for the expected project."""


@dataclass(frozen=True)
class FirebaseIdentity:
    project_id: str
    # "" when the project does not use Identity Platform multi-tenancy.
    firebase_tenant: str
    subject: str
    # The sign-in's email, as the identity provider gave it to Firebase. Read only by
    # `GET /guide/groups`, to find the person's SCIM groups (#58); never stored.
    email: str | None = None


def unverified_target(token: str) -> tuple[str, str]:
    """(project, Identity Platform tenant) the token *claims* to be for, read
    without verifying it. Only for choosing which connection to verify against;
    nothing is decided on it."""
    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError:
        raise FirebaseTokenError("Not a token")
    aud = claims.get("aud")
    firebase = claims.get("firebase")
    tenant = firebase.get("tenant", "") if isinstance(firebase, dict) else ""
    if not isinstance(aud, str) or not aud or not isinstance(tenant, str):
        raise FirebaseTokenError("Token names no project")
    return aud, tenant


async def fetch_google_certs() -> tuple[dict[str, str], int]:
    """Google's current signing certificates, and for how long they may be kept."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(CERTS_URL)
        response.raise_for_status()
    match = _MAX_AGE_RE.search(response.headers.get("cache-control", ""))
    max_age = int(match.group(1)) if match else _DEFAULT_MAX_AGE
    certs = response.json()
    if not isinstance(certs, dict) or not all(isinstance(v, str) for v in certs.values()):
        raise FirebaseTokenError("Unexpected certificate document")
    return certs, max_age


class FirebaseTokenVerifier:
    """Verifies Firebase ID tokens, caching Google's certificates for as long as
    Google says they may be kept."""

    def __init__(
        self,
        fetch: CertFetcher = fetch_google_certs,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._certs: dict[str, str] = {}
        self._expires_at = 0.0

    async def _cert(self, kid: str) -> str:
        if self._clock() >= self._expires_at or kid not in self._certs:
            try:
                certs, max_age = await self._fetch()
            except (httpx.HTTPError, ValueError) as error:
                raise FirebaseTokenError("Signing keys unavailable") from error
            self._certs = certs
            self._expires_at = self._clock() + max_age
        cert = self._certs.get(kid)
        if cert is None:
            raise FirebaseTokenError("Unknown signing key")
        return cert

    async def verify(self, token: str, project_id: str) -> FirebaseIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except JWTError:
            raise FirebaseTokenError("Not a token")
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise FirebaseTokenError("Not signed the way Firebase signs")
        cert = await self._cert(header["kid"])
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                cert,
                algorithms=["RS256"],
                audience=project_id,
                issuer=ISSUER_PREFIX + project_id,
                options={"leeway": _LEEWAY_SECONDS, "require_exp": True, "require_iat": True},
            )
        except JWTError:
            raise FirebaseTokenError("Invalid or expired token")
        now = self._clock()
        if claims["iat"] > now + _LEEWAY_SECONDS:
            raise FirebaseTokenError("Issued in the future")
        auth_time = claims.get("auth_time")
        if not isinstance(auth_time, int | float) or auth_time > now + _LEEWAY_SECONDS:
            raise FirebaseTokenError("No sign-in time")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise FirebaseTokenError("No subject")
        firebase = claims.get("firebase")
        tenant = firebase.get("tenant", "") if isinstance(firebase, dict) else ""
        if not isinstance(tenant, str):
            raise FirebaseTokenError("Unreadable tenant")
        email = claims.get("email")
        return FirebaseIdentity(
            project_id=project_id,
            firebase_tenant=tenant,
            subject=subject,
            email=email.strip() if isinstance(email, str) and email.strip() else None,
        )


_verifier = FirebaseTokenVerifier()


def get_firebase_verifier() -> FirebaseTokenVerifier:
    """FastAPI dependency; tests override it with a verifier fed local keys."""
    return _verifier
