"""Entra ID SSO login service (PRO-55): id-token signature and claim
validation + provision-or-link (link-only policy)."""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest
from jose import jwt

from app.models.user import Role, User, UserRole
from app.services import sso_login
from tests.conftest import TestSessionLocal
from tests.unit import entra_keys

CLIENT_ID = "00000000-aaaa-bbbb-cccc-clientid000001"
ISS = "https://login.microsoftonline.com/tid-123/v2.0"


def _make_id_token(claims: dict) -> str:
    """An id-token signed with the local stand-in for Microsoft's key."""
    return entra_keys.id_token(claims)


def _seg(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _claims(**over) -> dict:
    base = {
        "iss": ISS,
        "aud": CLIENT_ID,
        "oid": "entra-oid-abc",
        "email": "person@corp.example",
        "name": "Person Example",
        "nonce": "nonce-xyz",
        "exp": 9999999999,
    }
    base.update(over)
    return base


# ─── claim decode + validation ───────────────────────────────────────


def test_decode_id_token_claims_roundtrip() -> None:
    token = _make_id_token(_claims())
    claims = sso_login.decode_id_token_claims(token)
    assert claims["oid"] == "entra-oid-abc"
    assert claims["aud"] == CLIENT_ID


def test_validate_claims_accepts_valid() -> None:
    sso_login.validate_claims(
        _claims(), expected_audience=CLIENT_ID, expected_nonce="nonce-xyz", now_ts=1000
    )


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "someone-elses-client"},
        {"iss": "https://evil.example/v2.0"},
        {"nonce": "wrong-nonce"},
        {"exp": 500},
    ],
)
def test_validate_claims_rejects(override: dict) -> None:
    with pytest.raises(sso_login.SSOIdTokenError):
        sso_login.validate_claims(
            _claims(**override),
            expected_audience=CLIENT_ID,
            expected_nonce="nonce-xyz",
            now_ts=1000,
        )


# ─── signature verification against Microsoft's keys ─────────────────


async def test_verify_signature_accepts_good_token() -> None:
    await entra_keys.key_set().verify_signature(_make_id_token(_claims()))


async def test_verify_signature_rejects_tampered_payload() -> None:
    header, _payload, signature = _make_id_token(_claims()).split(".")
    tampered = f"{header}.{_seg(_claims(oid='someone-else'))}.{signature}"
    with pytest.raises(sso_login.SSOIdTokenError):
        await entra_keys.key_set().verify_signature(tampered)


async def test_verify_signature_rejects_token_signed_by_another_key() -> None:
    token = entra_keys.id_token(_claims(), key=entra_keys.OTHER_PRIVATE_PEM)
    with pytest.raises(sso_login.SSOIdTokenError):
        await entra_keys.key_set().verify_signature(token)


async def test_verify_signature_rejects_unknown_kid() -> None:
    token = entra_keys.id_token(_claims(), kid="not-a-microsoft-kid")
    with pytest.raises(sso_login.SSOIdTokenError, match="unknown signing key"):
        await entra_keys.key_set().verify_signature(token)


async def test_verify_signature_rejects_alg_none() -> None:
    token = f"{_seg({'alg': 'none', 'kid': entra_keys.KID})}.{_seg(_claims())}."
    with pytest.raises(sso_login.SSOIdTokenError):
        await entra_keys.key_set().verify_signature(token)


async def test_verify_signature_rejects_hs256() -> None:
    token = jwt.encode(
        _claims(), "shared-secret", algorithm="HS256", headers={"kid": entra_keys.KID}
    )
    with pytest.raises(sso_login.SSOIdTokenError):
        await entra_keys.key_set().verify_signature(token)


async def test_verify_signature_rejects_hs256_keyed_with_the_public_key() -> None:
    public = json.dumps(entra_keys.PUBLIC_JWK)
    token = jwt.encode(_claims(), public, algorithm="HS256", headers={"kid": entra_keys.KID})
    with pytest.raises(sso_login.SSOIdTokenError):
        await entra_keys.key_set().verify_signature(token)


async def test_verify_signature_rejects_token_without_kid() -> None:
    token = jwt.encode(_claims(), entra_keys.PRIVATE_PEM, algorithm="RS256")
    with pytest.raises(sso_login.SSOIdTokenError):
        await entra_keys.key_set().verify_signature(token)


async def test_verify_signature_rejects_non_jwt() -> None:
    with pytest.raises(sso_login.SSOIdTokenError):
        await entra_keys.key_set().verify_signature("not-a-jwt")


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("down"), ValueError("Unexpected key document")],
)
async def test_verify_signature_fails_closed_when_keys_unavailable(error: Exception) -> None:
    async def fetch():
        raise error

    with pytest.raises(sso_login.SSOIdTokenError, match="signing keys unavailable"):
        await sso_login.EntraKeySet(fetch=fetch).verify_signature(_make_id_token(_claims()))


class _KeyEndpoint:
    """Microsoft's key endpoint, counting how often Atlas asks, on a clock the
    test moves."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.calls = 0
        self.keys = {entra_keys.KID: entra_keys.jwk_for(entra_keys.KID)}

    async def fetch(self):
        self.calls += 1
        return dict(self.keys), 3600

    def key_set(self) -> sso_login.EntraKeySet:
        return sso_login.EntraKeySet(fetch=self.fetch, clock=lambda: self.now)


async def test_keys_are_cached_for_max_age() -> None:
    endpoint = _KeyEndpoint()
    keys = endpoint.key_set()
    token = _make_id_token(_claims())
    await keys.verify_signature(token)
    endpoint.now += 3599
    await keys.verify_signature(token)
    assert endpoint.calls == 1
    endpoint.now += 2
    await keys.verify_signature(token)
    assert endpoint.calls == 2


async def test_unknown_kid_forces_at_most_one_refresh_a_minute() -> None:
    endpoint = _KeyEndpoint()
    keys = endpoint.key_set()
    await keys.verify_signature(_make_id_token(_claims()))
    assert endpoint.calls == 1

    unknown = entra_keys.id_token(_claims(), kid="unknown-kid")
    with pytest.raises(sso_login.SSOIdTokenError):
        await keys.verify_signature(unknown)
    assert endpoint.calls == 2  # one early fetch, in case Microsoft rotated a key in
    with pytest.raises(sso_login.SSOIdTokenError):
        await keys.verify_signature(unknown)
    assert endpoint.calls == 2  # throttled: no second early fetch within the minute

    endpoint.now += sso_login.FORCED_REFRESH_INTERVAL
    with pytest.raises(sso_login.SSOIdTokenError):
        await keys.verify_signature(unknown)
    assert endpoint.calls == 3


async def test_forced_refresh_picks_up_a_rotated_key() -> None:
    endpoint = _KeyEndpoint()
    keys = endpoint.key_set()
    await keys.verify_signature(_make_id_token(_claims()))
    endpoint.keys["rotated-kid"] = entra_keys.jwk_for("rotated-kid", entra_keys.OTHER_PUBLIC_JWK)
    rotated = entra_keys.id_token(_claims(), kid="rotated-kid", key=entra_keys.OTHER_PRIVATE_PEM)
    await keys.verify_signature(rotated)
    assert endpoint.calls == 2


async def test_fetch_microsoft_keys_reads_jwks_and_max_age() -> None:
    seen: list[str] = []
    document = {
        "keys": [
            entra_keys.jwk_for(entra_keys.KID),
            {"kty": "EC", "kid": "ec-key", "use": "sig"},
            {**entra_keys.jwk_for("enc-key"), "use": "enc"},
            {"kty": "RSA"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200, json=document, headers={"cache-control": "public, max-age=86400"}
        )

    keys, max_age = await sso_login.fetch_microsoft_keys(httpx.MockTransport(handler))
    assert seen == [sso_login.JWKS_URL]
    assert list(keys) == [entra_keys.KID]
    assert max_age == 86400


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("max-age=99999999", 86400),
        ("max-age=0", 60),
        ("public, max-age=7200", 7200),
        (None, 3600),
    ],
)
async def test_fetch_microsoft_keys_clamps_max_age(header: str | None, expected: int) -> None:
    headers = {"cache-control": header} if header else {}
    document = {"keys": [entra_keys.jwk_for(entra_keys.KID)]}
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=document, headers=headers)
    )
    _keys, max_age = await sso_login.fetch_microsoft_keys(transport)
    assert max_age == expected


async def test_fetch_microsoft_keys_raises_on_http_error() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await sso_login.fetch_microsoft_keys(transport)


async def test_fetch_microsoft_keys_rejects_malformed_document() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=["nope"]))
    with pytest.raises(ValueError, match="Unexpected key document"):
        await sso_login.fetch_microsoft_keys(transport)


# ─── login-state HMAC roundtrip ──────────────────────────────────────


def test_login_state_roundtrip() -> None:
    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="nonce-xyz")
    payload = sso_login.decode_login_state(signing_secret="s3cret", state=state)
    assert payload["nonce"] == "nonce-xyz"


def test_login_state_tamper_rejected() -> None:
    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="nonce-xyz")
    with pytest.raises(sso_login.SSOLoginStateError):
        sso_login.decode_login_state(signing_secret="different", state=state)


# ─── provision-or-link (link-only) ───────────────────────────────────


async def _seed_user(*, email: str, oid: str | None, active: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    async with TestSessionLocal() as session:
        user = User(
            id=uid,
            email=email,
            hashed_password=None,
            full_name="Seed",
            entra_object_id=oid,
            is_active=active,
            is_test_data=True,
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(id=uuid.uuid4(), user_id=uid, role=Role.ANALYST))
        await session.commit()
    return uid


async def test_resolve_matches_by_entra_object_id() -> None:
    uid = await _seed_user(email="byoid@corp.example", oid="oid-match")
    async with TestSessionLocal() as session:
        user = await sso_login.resolve_sso_user(
            session, entra_object_id="oid-match", email="byoid@corp.example"
        )
        assert user.id == uid


async def test_resolve_matches_by_email_and_backfills_oid() -> None:
    uid = await _seed_user(email="byemail@corp.example", oid=None)
    async with TestSessionLocal() as session:
        user = await sso_login.resolve_sso_user(
            session, entra_object_id="oid-new", email="byemail@corp.example"
        )
        assert user.id == uid
        assert user.entra_object_id == "oid-new"
        await session.commit()  # caller commits; service only flushes

    async with TestSessionLocal() as session:
        from sqlalchemy import select

        refreshed = (await session.execute(select(User).where(User.id == uid))).scalar_one()
        assert refreshed.entra_object_id == "oid-new"


async def test_resolve_unknown_user_raises_not_provisioned() -> None:
    async with TestSessionLocal() as session:
        with pytest.raises(sso_login.SSOAccountNotProvisionedError):
            await sso_login.resolve_sso_user(
                session, entra_object_id="nope", email="stranger@corp.example"
            )


async def test_resolve_inactive_user_raises_not_provisioned() -> None:
    await _seed_user(email="inactive@corp.example", oid="oid-inactive", active=False)
    async with TestSessionLocal() as session:
        with pytest.raises(sso_login.SSOAccountNotProvisionedError):
            await sso_login.resolve_sso_user(
                session, entra_object_id="oid-inactive", email="inactive@corp.example"
            )


# ─── end-to-end orchestration (code -> validated claims -> session) ──

import types  # noqa: E402

_SETTINGS = types.SimpleNamespace(
    microsoft_client_id=CLIENT_ID,
    microsoft_client_secret="secret",
    microsoft_signing_secret="s3cret",
    microsoft_sso_redirect_url="http://localhost:8000/api/v1/auth/sso/microsoft/callback",
)


def _token_client(id_token: str) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "ms-access",
                "id_token": id_token,
                "expires_in": 3600,
                "scope": "openid profile email",
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_complete_sso_login_issues_session(monkeypatch) -> None:
    uid = await _seed_user(email="e2e@corp.example", oid="oid-e2e")
    monkeypatch.setattr(
        "app.services.auth_service.store_refresh_token",
        _async_noop,
    )
    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="n-1")
    id_token = _make_id_token(_claims(oid="oid-e2e", email="e2e@corp.example", nonce="n-1"))

    async with TestSessionLocal() as session:
        async with _token_client(id_token) as client:
            result = await sso_login.complete_sso_login(
                session,
                code="auth-code",
                state=state,
                settings=_SETTINGS,
                client=client,
                now_ts=1000,
                key_set=entra_keys.key_set(),
            )
    assert result["token_type"] == "bearer"
    assert result["access_token"]
    assert result["refresh_token"]
    # the issued access token encodes the resolved user
    from app.auth.jwt import decode_token

    assert decode_token(result["access_token"])["sub"] == str(uid)


async def test_complete_sso_login_unknown_user_raises(monkeypatch) -> None:
    monkeypatch.setattr("app.services.auth_service.store_refresh_token", _async_noop)
    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="n-2")
    id_token = _make_id_token(_claims(oid="stranger", email="stranger@corp.example", nonce="n-2"))

    async with TestSessionLocal() as session:
        async with _token_client(id_token) as client:
            with pytest.raises(sso_login.SSOAccountNotProvisionedError):
                await sso_login.complete_sso_login(
                    session,
                    code="auth-code",
                    state=state,
                    settings=_SETTINGS,
                    client=client,
                    now_ts=1000,
                    key_set=entra_keys.key_set(),
                )


async def test_complete_sso_login_rejects_unsigned_token_before_linking(monkeypatch) -> None:
    await _seed_user(email="unsigned@corp.example", oid="oid-unsigned")
    monkeypatch.setattr("app.services.auth_service.store_refresh_token", _async_noop)
    state = sso_login.encode_login_state(signing_secret="s3cret", nonce="n-3")
    claims = _claims(oid="oid-unsigned", email="unsigned@corp.example", nonce="n-3")
    unsigned = f"{_seg({'alg': 'RS256', 'kid': entra_keys.KID})}.{_seg(claims)}.sig"

    async with TestSessionLocal() as session:
        async with _token_client(unsigned) as client:
            with pytest.raises(sso_login.SSOIdTokenError):
                await sso_login.complete_sso_login(
                    session,
                    code="auth-code",
                    state=state,
                    settings=_SETTINGS,
                    client=client,
                    now_ts=1000,
                    key_set=entra_keys.key_set(),
                )


async def _async_noop(*_args, **_kwargs) -> None:
    return None
