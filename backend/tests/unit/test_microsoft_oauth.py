"""Unit tests for app.services.microsoft_oauth.

Mirrors test_slack_oauth.py — state HMAC roundtrip + tampering,
authorize URL shape, code exchange via httpx.MockTransport, refresh
flow, id_token tid extraction.
"""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest

from app.models.integration import IntegrationProvider
from app.services import microsoft_oauth

pytestmark = [pytest.mark.unit]


# ─── State ───────────────────────────────────────────────────────────


class TestState:
    def test_roundtrip(self) -> None:
        tenant = uuid.UUID("11111111-1111-1111-1111-111111111111")
        user = uuid.UUID("22222222-2222-2222-2222-222222222222")
        state = microsoft_oauth.encode_state(
            signing_secret="shh",
            tenant_id=tenant,
            user_id=user,
            provider=IntegrationProvider.MICROSOFT_ENTRA_ID,
            nonce="abc",
        )
        decoded = microsoft_oauth.decode_state(signing_secret="shh", state=state)
        assert decoded["tenant_id"] == str(tenant)
        assert decoded["provider"] == "MICROSOFT_ENTRA_ID"
        assert decoded["nonce"] == "abc"

    def test_wrong_secret_rejected(self) -> None:
        state = microsoft_oauth.encode_state(
            signing_secret="real",
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider=IntegrationProvider.MICROSOFT_INTUNE,
            nonce="n",
        )
        with pytest.raises(microsoft_oauth.MicrosoftOAuthStateError):
            microsoft_oauth.decode_state(signing_secret="forged", state=state)

    def test_tampered_provider_rejected(self) -> None:
        """An attacker can't swap MICROSOFT_INTUNE for MICROSOFT_ENTRA_ID."""
        state = microsoft_oauth.encode_state(
            signing_secret="shh",
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider=IntegrationProvider.MICROSOFT_INTUNE,
            nonce="n",
        )
        # Tamper the encoded portion.
        encoded, sig = state.rsplit(".", 1)
        tampered = encoded[:-2] + "AA." + sig
        with pytest.raises(microsoft_oauth.MicrosoftOAuthStateError):
            microsoft_oauth.decode_state(signing_secret="shh", state=tampered)


# ─── Authorize URL ───────────────────────────────────────────────────


class TestAuthorizeUrl:
    def test_contains_required_params(self) -> None:
        from urllib.parse import quote

        url = microsoft_oauth.build_authorize_url(
            client_id="cid-x",
            redirect_url="https://atlas.example/cb",
            scopes=["openid", "User.Read.All"],
            state="state-xyz",
        )
        assert url.startswith(microsoft_oauth.MS_AUTHORIZE_URL + "?")
        assert "client_id=cid-x" in url
        assert "response_type=code" in url
        assert "state=state-xyz" in url
        # scopes are space-separated → URL-encoded as %20 or +
        assert quote("User.Read.All", safe="") in url

    def test_forces_consent_prompt(self) -> None:
        url = microsoft_oauth.build_authorize_url(
            client_id="cid",
            redirect_url="https://x/cb",
            scopes=["openid"],
            state="s",
        )
        assert "prompt=consent" in url


# ─── Code exchange ───────────────────────────────────────────────────


def _id_token_with_tid(tid: str) -> str:
    """Build a fake unsigned id_token for tests."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"tid": tid}).encode()).decode().rstrip("=")
    return f"{header}.{payload}."


class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "access_token": "at-1234",
                    "refresh_token": "rt-5678",
                    "expires_in": 3599,
                    "scope": "openid User.Read.All",
                    "id_token": _id_token_with_tid("aad-tenant-uuid"),
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await microsoft_oauth.exchange_code(
                client=client,
                client_id="cid",
                client_secret="csec",
                redirect_url="https://x/cb",
                code="abc",
                scopes=["openid", "User.Read.All"],
            )
        assert result["access_token"] == "at-1234"
        assert result["refresh_token"] == "rt-5678"
        assert result["expires_in"] == 3599
        assert "User.Read.All" in result["scope"]

    @pytest.mark.asyncio
    async def test_error_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "AADSTS70008: ...",
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await microsoft_oauth.exchange_code(
                    client=client,
                    client_id="cid",
                    client_secret="csec",
                    redirect_url="https://x/cb",
                    code="abc",
                    scopes=["openid"],
                )

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "weird"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(microsoft_oauth.MicrosoftOAuthError):
                await microsoft_oauth.exchange_code(
                    client=client,
                    client_id="cid",
                    client_secret="csec",
                    redirect_url="https://x/cb",
                    code="abc",
                    scopes=["openid"],
                )


# ─── Refresh ─────────────────────────────────────────────────────────


class TestRefresh:
    @pytest.mark.asyncio
    async def test_returns_new_refresh_token(self) -> None:
        """Microsoft rotates refresh tokens — caller must persist new."""

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "access_token": "at-new",
                    "refresh_token": "rt-NEW",
                    "expires_in": 3600,
                    "scope": "openid",
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await microsoft_oauth.refresh_access_token(
                client=client,
                client_id="cid",
                client_secret="csec",
                refresh_token="rt-old",
                scopes=["openid"],
            )
        assert result["access_token"] == "at-new"
        assert result["refresh_token"] == "rt-NEW"

    @pytest.mark.asyncio
    async def test_refresh_falls_back_to_old(self) -> None:
        """If Microsoft omits a new refresh_token, keep the old one."""

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "access_token": "at-new",
                    "expires_in": 3600,
                    "scope": "openid",
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await microsoft_oauth.refresh_access_token(
                client=client,
                client_id="cid",
                client_secret="csec",
                refresh_token="rt-old",
                scopes=["openid"],
            )
        assert result["refresh_token"] == "rt-old"


# ─── id_token parsing ────────────────────────────────────────────────


class TestIdTokenParse:
    def test_extracts_tid(self) -> None:
        token = _id_token_with_tid("aad-uuid-here")
        assert microsoft_oauth.parse_tenant_id_from_id_token(token) == "aad-uuid-here"

    def test_malformed_returns_none(self) -> None:
        assert microsoft_oauth.parse_tenant_id_from_id_token("garbage") is None

    def test_no_tid_returns_none(self) -> None:
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        payload = (
            base64.urlsafe_b64encode(json.dumps({"other": "claim"}).encode()).decode().rstrip("=")
        )
        token = f"{header}.{payload}."
        assert microsoft_oauth.parse_tenant_id_from_id_token(token) is None
