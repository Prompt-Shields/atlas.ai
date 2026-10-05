"""Firebase ID token verification (promptly-guide #37).

Atlas does not issue these tokens, so every check Firebase documents is Atlas's
to make: Google's key, RS256, the project as audience and in the issuer, not
expired, not from the future, a subject.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.auth.firebase_token import FirebaseTokenError, unverified_target
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


async def test_the_target_is_read_without_trusting_it() -> None:
    assert unverified_target(keys.token(firebase_tenant="t1")) == (keys.PROJECT, "t1")
    with pytest.raises(FirebaseTokenError):
        unverified_target("not a token")
