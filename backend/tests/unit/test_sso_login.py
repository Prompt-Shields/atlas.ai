"""Entra ID SSO login service (PRO-55): id-token claim validation +
provision-or-link (link-only policy)."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from app.models.user import Role, User, UserRole
from app.services import sso_login
from tests.conftest import TestSessionLocal

CLIENT_ID = "00000000-aaaa-bbbb-cccc-clientid000001"
ISS = "https://login.microsoftonline.com/tid-123/v2.0"


def _make_id_token(claims: dict) -> str:
    """Craft an unsigned JWT-shaped token (header.payload.sig)."""

    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.sig"


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

import httpx  # noqa: E402

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
                )


async def _async_noop(*_args, **_kwargs) -> None:
    return None
