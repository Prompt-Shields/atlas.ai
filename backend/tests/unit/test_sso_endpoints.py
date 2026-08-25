"""HTTP wiring for Entra SSO login (PRO-55): /auth/sso/microsoft/{login,callback}."""

from __future__ import annotations

import base64
import json
import uuid

import pytest_asyncio
from httpx import AsyncClient

from app.config import get_settings
from app.models.user import Role, User, UserRole
from app.services import sso_login
from tests.conftest import TestSessionLocal

CLIENT_ID = "client-id-sso-001"


_CODE_STORE: dict[str, dict] = {}


async def _fake_store_code(code: str, tokens: dict, ttl_seconds: int = 120) -> None:
    _CODE_STORE[code] = tokens


async def _fake_consume_code(code: str) -> dict | None:
    return _CODE_STORE.pop(code, None)  # single-use, mirrors Redis GETDEL


@pytest_asyncio.fixture
async def ms_settings(monkeypatch):
    _CODE_STORE.clear()
    s = get_settings()
    monkeypatch.setattr(s, "microsoft_client_id", CLIENT_ID, raising=False)
    monkeypatch.setattr(s, "microsoft_client_secret", "secret", raising=False)
    monkeypatch.setattr(s, "microsoft_signing_secret", "sign-secret", raising=False)
    monkeypatch.setattr(
        s,
        "microsoft_sso_redirect_url",
        "http://localhost:8000/api/v1/auth/sso/microsoft/callback",
        raising=False,
    )
    monkeypatch.setattr(s, "frontend_base_url", "http://localhost:3000", raising=False)
    monkeypatch.setattr("app.services.auth_service.store_refresh_token", _async_noop)
    monkeypatch.setattr("app.routers.auth.store_sso_code", _fake_store_code)
    monkeypatch.setattr("app.routers.auth.consume_sso_code", _fake_consume_code)
    return s


def _id_token(**claims) -> str:
    base = {
        "iss": "https://login.microsoftonline.com/tid/v2.0",
        "aud": CLIENT_ID,
        "oid": "oid-http",
        "email": "http@corp.example",
        "name": "Http User",
        "exp": 9999999999,
    }
    base.update(claims)

    def seg(o: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(base)}.sig"


async def test_login_redirects_to_microsoft(client: AsyncClient, ms_settings) -> None:
    resp = await client.get("/api/v1/auth/sso/microsoft/login")
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert loc.startswith("https://login.microsoftonline.com/")
    assert "state=" in loc and "nonce=" in loc


async def test_callback_issues_session_for_linked_user(
    client: AsyncClient, ms_settings, monkeypatch
) -> None:
    uid = uuid.uuid4()
    async with TestSessionLocal() as session:
        session.add(
            User(
                id=uid,
                email="http@corp.example",
                hashed_password=None,
                full_name="Http User",
                entra_object_id="oid-http",
                is_active=True,
                is_test_data=True,
            )
        )
        await session.flush()
        session.add(UserRole(id=uuid.uuid4(), user_id=uid, role=Role.ANALYST))
        await session.commit()

    state = sso_login.encode_login_state(signing_secret="sign-secret", nonce="nn")

    async def fake_exchange(**_kwargs):
        return {"id_token": _id_token(nonce="nn"), "access_token": "a", "expires_in": 3600}

    monkeypatch.setattr("app.services.microsoft_oauth.exchange_code", fake_exchange)

    resp = await client.get(
        "/api/v1/auth/sso/microsoft/callback", params={"code": "c", "state": state}
    )
    # One-time-code handoff: redirect to the SPA with a code (no tokens in URL).
    assert resp.status_code in (302, 307), resp.text
    loc = resp.headers["location"]
    assert loc.startswith("http://localhost:3000/auth/sso/callback?code=")
    code = loc.rsplit("code=", 1)[1]
    # The code resolves to a real session via the exchange endpoint.
    ex = await client.post("/api/v1/auth/sso/exchange", json={"code": code})
    assert ex.status_code == 200, ex.text
    assert ex.json()["token_type"] == "bearer"
    # Single-use: a second exchange of the same code fails.
    assert (await client.post("/api/v1/auth/sso/exchange", json={"code": code})).status_code == 401


async def test_callback_unknown_user_redirects_with_error(
    client: AsyncClient, ms_settings, monkeypatch
) -> None:
    state = sso_login.encode_login_state(signing_secret="sign-secret", nonce="nn")

    async def fake_exchange(**_kwargs):
        return {
            "id_token": _id_token(nonce="nn", oid="ghost", email="ghost@corp.example"),
            "access_token": "a",
            "expires_in": 3600,
        }

    monkeypatch.setattr("app.services.microsoft_oauth.exchange_code", fake_exchange)

    resp = await client.get(
        "/api/v1/auth/sso/microsoft/callback", params={"code": "c", "state": state}
    )
    assert resp.status_code in (302, 307)
    assert "sso_error=" in resp.headers["location"]


async def test_exchange_unknown_code_is_unauthorized(client: AsyncClient, ms_settings) -> None:
    resp = await client.post("/api/v1/auth/sso/exchange", json={"code": "nope"})
    assert resp.status_code == 401


async def _async_noop(*_args, **_kwargs) -> None:
    return None
