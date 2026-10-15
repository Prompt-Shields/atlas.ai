"""Firebase ID token verification (promptly-guide #37).

Atlas does not issue these tokens, so every check Firebase documents is Atlas's
to make: Google's key, RS256, the project as audience and in the issuer, not
expired, not from the future, a subject.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

import pytest

from app.auth.firebase_token import (
    FORCED_REFRESH_INTERVAL,
    FirebaseTokenError,
    FirebaseTokenVerifier,
    unverified_target,
)
from tests.unit import firebase_keys as keys

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_a_token_from_the_project_is_accepted() -> None:
    identity = await keys.verifier().verify(keys.token(firebase_tenant="acme-x1"), keys.PROJECT)
    assert identity.project_id == keys.PROJECT
    assert identity.firebase_tenant == "acme-x1"
    assert identity.subject == "firebase-uid-1"


async def test_no_tenant_reads_as_empty() -> None:
    identity = await keys.verifier().verify(keys.token(), keys.PROJECT)
    assert identity.firebase_tenant == ""


@pytest.mark.parametrize(
    ("overrides", "omit", "verified"),
    [
        pytest.param({"email_verified": True}, (), True, id="true"),
        pytest.param({"email_verified": False}, (), False, id="false"),
        pytest.param({"email_verified": "true"}, (), False, id="the-string-true"),
        pytest.param({}, ("email_verified",), False, id="missing"),
    ],
)
async def test_an_email_counts_as_verified_only_when_the_claim_is_true(
    overrides, omit, verified
) -> None:
    token = keys.token(overrides=overrides, omit=omit)
    identity = await keys.verifier().verify(token, keys.PROJECT)
    assert identity.email == "kari@example.com"
    assert identity.email_verified is verified


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"aud": "someone-else"}, id="other-audience"),
        pytest.param({"iss": "https://securetoken.google.com/someone-else"}, id="other-issuer"),
        pytest.param({"exp": int(time.time()) - 3600}, id="expired"),
        pytest.param({"iat": int(time.time()) + 3600}, id="issued-in-future"),
        pytest.param({"auth_time": int(time.time()) + 3600}, id="signed-in-in-future"),
        pytest.param({"sub": ""}, id="no-subject"),
    ],
)
async def test_a_token_that_is_not_for_this_project_or_not_current_is_refused(bad) -> None:
    with pytest.raises(FirebaseTokenError):
        await keys.verifier().verify(keys.token(overrides=bad), keys.PROJECT)


async def test_a_token_signed_by_another_key_is_refused() -> None:
    forged = keys.token(key=keys.OTHER_PRIVATE_PEM)
    with pytest.raises(FirebaseTokenError):
        await keys.verifier().verify(forged, keys.PROJECT)


async def test_an_unknown_key_id_is_refused() -> None:
    with pytest.raises(FirebaseTokenError):
        await keys.verifier().verify(keys.token(kid="not-a-google-key"), keys.PROJECT)


async def test_a_symmetric_token_is_refused_whatever_it_claims() -> None:
    now = int(time.time())
    claims = {
        "iss": "https://securetoken.google.com/" + keys.PROJECT,
        "aud": keys.PROJECT,
        "sub": "x",
        "iat": now,
        "exp": now + 60,
        "auth_time": now,
    }

    # The classic confusion: HS256 "signed" with the public certificate as secret.
    # Built by hand, since jose itself refuses to sign with a certificate.
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    signing_input = part({"alg": "HS256", "kid": keys.KID}) + "." + part(claims)
    mac = hmac.new(keys.CERT_PEM.encode(), signing_input.encode(), hashlib.sha256).digest()
    forged = signing_input + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    with pytest.raises(FirebaseTokenError):
        await keys.verifier().verify(forged, keys.PROJECT)


async def test_keys_are_fetched_again_when_a_new_one_appears() -> None:
    served: dict[str, str] = {}
    fetches = 0

    async def fetch() -> tuple[dict[str, str], int]:
        nonlocal fetches
        fetches += 1
        return dict(served), 3600

    from app.auth.firebase_token import FirebaseTokenVerifier

    verifier = FirebaseTokenVerifier(fetch=fetch)
    with pytest.raises(FirebaseTokenError):
        await verifier.verify(keys.token(), keys.PROJECT)
    served[keys.KID] = keys.CERT_PEM  # Google rotated a key in
    identity = await verifier.verify(keys.token(), keys.PROJECT)
    assert identity.subject == "firebase-uid-1"
    assert fetches == 2


class _CountingGoogle:
    """Google's certificate endpoint, counting how often Atlas asks, on a clock the
    test moves."""

    def __init__(self, served: dict[str, str]) -> None:
        self.served = served
        self.fetches = 0
        self.now = time.time()

    async def fetch(self) -> tuple[dict[str, str], int]:
        self.fetches += 1
        await asyncio.sleep(0)  # let other requests in while this one is under way
        return dict(self.served), 3600

    def verifier(self) -> FirebaseTokenVerifier:
        return FirebaseTokenVerifier(fetch=self.fetch, clock=lambda: self.now)


async def test_unknown_key_ids_fetch_googles_keys_again_at_most_once_an_interval() -> None:
    google = _CountingGoogle({keys.KID: keys.CERT_PEM})
    verifier = google.verifier()
    await verifier.verify(keys.token(), keys.PROJECT)
    assert google.fetches == 1

    async def unknown(n: int) -> None:
        with pytest.raises(FirebaseTokenError):
            await verifier.verify(keys.token(kid=f"made-up-{n}"), keys.PROJECT)

    await asyncio.gather(*(unknown(n) for n in range(50)))
    for n in range(50, 100):
        await unknown(n)
    assert google.fetches == 2
    # Tokens with a known key still verify, from the cache.
    await verifier.verify(keys.token(), keys.PROJECT)
    assert google.fetches == 2


async def test_a_key_google_rotated_in_is_accepted() -> None:
    google = _CountingGoogle({"old-kid": keys.CERT_PEM})
    verifier = google.verifier()
    await verifier.verify(keys.token(kid="old-kid"), keys.PROJECT)
    google.served[keys.KID] = keys.CERT_PEM
    # Requests arriving together share the one early fetch.
    identities = await asyncio.gather(
        *(verifier.verify(keys.token(), keys.PROJECT) for _ in range(5))
    )
    assert {i.subject for i in identities} == {"firebase-uid-1"}
    assert google.fetches == 2


async def test_another_early_fetch_is_allowed_once_the_interval_has_passed() -> None:
    google = _CountingGoogle({"old-kid": keys.CERT_PEM})
    verifier = google.verifier()
    await verifier.verify(keys.token(kid="old-kid"), keys.PROJECT)
    with pytest.raises(FirebaseTokenError):
        await verifier.verify(keys.token(), keys.PROJECT)
    assert google.fetches == 2
    google.served[keys.KID] = keys.CERT_PEM
    google.now += FORCED_REFRESH_INTERVAL - 1
    with pytest.raises(FirebaseTokenError):
        await verifier.verify(keys.token(), keys.PROJECT)
    assert google.fetches == 2
    google.now += 1
    identity = await verifier.verify(keys.token(), keys.PROJECT)
    assert identity.subject == "firebase-uid-1"
    assert google.fetches == 3


async def test_the_target_is_read_without_trusting_it() -> None:
    assert unverified_target(keys.token(firebase_tenant="t1")) == (keys.PROJECT, "t1")
    with pytest.raises(FirebaseTokenError):
        unverified_target("not a token")
