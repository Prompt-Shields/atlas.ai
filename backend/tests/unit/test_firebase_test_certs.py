"""`GUIDE_TEST_FIREBASE_CERTS`, the E2E stack's stand-in for Google's keys.

It makes Atlas trust a signing key of the caller's choosing, so it must do nothing,
and stop start-up, anywhere but in testing.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.auth import firebase_token
from app.auth.firebase_token import FirebaseTokenError, fetch_google_certs
from app.config import Environment, Settings
from tests.unit import firebase_keys as keys

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

CERTS = json.dumps({keys.KID: keys.CERT_PEM})


def _settings(env: Environment, certs: str = "") -> Settings:
    return Settings(
        app_env=env,
        app_debug=False,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        jwt_secret_key="x" * 40,
        backend_cors_origins="https://atlas.example",
        guide_test_firebase_certs=certs,
    )


@pytest.fixture
def use_settings(monkeypatch: pytest.MonkeyPatch):
    def use(settings: Settings) -> None:
        monkeypatch.setattr(firebase_token, "get_settings", lambda: settings)
        monkeypatch.setattr(firebase_token, "_test_verifier", None)

    return use


@pytest.mark.parametrize(
    "env", [Environment.DEVELOPMENT, Environment.STAGING, Environment.PRODUCTION]
)
async def test_refused_at_start_up_outside_testing(env: Environment) -> None:
    with pytest.raises(ValidationError, match="only be set when APP_ENV is testing"):
        _settings(env, CERTS)


@pytest.mark.parametrize("env", list(Environment))
async def test_unset_is_google_everywhere(env: Environment, use_settings) -> None:
    use_settings(_settings(env))
    verifier = firebase_token.get_firebase_verifier()
    assert verifier is firebase_token._verifier
    assert verifier._fetch is fetch_google_certs


@pytest.mark.parametrize("document", ["not json", "[]", "{}", '{"kid": 1}'])
async def test_a_document_that_is_not_certificates_is_refused(document: str) -> None:
    with pytest.raises(ValidationError):
        _settings(Environment.TESTING, document)


async def test_in_testing_tokens_are_verified_against_the_given_certificates(
    use_settings,
) -> None:
    use_settings(_settings(Environment.TESTING, CERTS))
    verifier = firebase_token.get_firebase_verifier()
    assert verifier is not firebase_token._verifier
    identity = await verifier.verify(keys.token(), keys.PROJECT)
    assert identity.subject == "firebase-uid-1"
    with pytest.raises(FirebaseTokenError):
        await verifier.verify(keys.token(key=keys.OTHER_PRIVATE_PEM), keys.PROJECT)


async def test_the_dependency_checks_the_environment_again(use_settings) -> None:
    # Settings refuses this combination; built past its validator, the dependency
    # still will not use the certificates.
    settings = _settings(Environment.TESTING, CERTS)
    settings.app_env = Environment.PRODUCTION
    use_settings(settings)
    with pytest.raises(RuntimeError):
        firebase_token.get_firebase_verifier()
