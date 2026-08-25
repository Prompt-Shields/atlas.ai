"""Unit tests for app.services.slack_oauth.

Covers:
  - State HMAC encode/decode roundtrip
  - State decode rejects tampered payloads
  - build_authorize_url assembles the correct querystring + scopes
  - exchange_code happy path + Slack-error path

`exchange_code` uses httpx; mocked via httpx.MockTransport.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.services import slack_oauth

pytestmark = [pytest.mark.unit]


class TestState:
    def test_roundtrip(self) -> None:
        tenant = uuid.UUID("11111111-1111-1111-1111-111111111111")
        user = uuid.UUID("22222222-2222-2222-2222-222222222222")
        state = slack_oauth.encode_state(
            signing_secret="shh",
            tenant_id=tenant,
            user_id=user,
            nonce="abc123",
        )
        decoded = slack_oauth.decode_state(signing_secret="shh", state=state)
        assert decoded["tenant_id"] == str(tenant)
        assert decoded["user_id"] == str(user)
        assert decoded["nonce"] == "abc123"

    def test_wrong_signing_secret_rejected(self) -> None:
        state = slack_oauth.encode_state(
            signing_secret="real",
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            nonce="n",
        )
        with pytest.raises(slack_oauth.OAuthStateError, match="HMAC"):
            slack_oauth.decode_state(signing_secret="forged", state=state)

    def test_tampered_payload_rejected(self) -> None:
        state = slack_oauth.encode_state(
            signing_secret="shh",
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            nonce="n",
        )
        encoded, sig = state.rsplit(".", 1)
        tampered = encoded[:-2] + "AA." + sig
        with pytest.raises(slack_oauth.OAuthStateError):
            slack_oauth.decode_state(signing_secret="shh", state=tampered)

    def test_missing_separator_rejected(self) -> None:
        with pytest.raises(slack_oauth.OAuthStateError, match="separator"):
            slack_oauth.decode_state(signing_secret="shh", state="no_dot")


class TestAuthorizeUrl:
    def test_includes_scopes(self) -> None:
        from urllib.parse import quote

        url = slack_oauth.build_authorize_url(
            client_id="client-x",
            redirect_url="https://atlas.example/cb",
            state="state-xyz",
        )
        assert url.startswith("https://slack.com/oauth/v2/authorize?")
        # All default scopes from SLACK_SCOPES present (URL-encoded).
        for scope in slack_oauth.SLACK_SCOPES:
            assert quote(scope, safe="") in url
        assert "client_id=client-x" in url
        assert "state=state-xyz" in url

    def test_custom_scopes_override(self) -> None:
        url = slack_oauth.build_authorize_url(
            client_id="c",
            redirect_url="https://x/cb",
            state="s",
            scopes=["chat:write"],
        )
        assert "scope=chat%3Awrite" in url
        # Default 'users:read' should NOT be present when overridden.
        assert "users%3Aread" not in url


class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-test-1234",
                    "bot_user_id": "U_BOT",
                    "team": {"id": "T123", "name": "Sample Workspace"},
                    "scope": "chat:write,im:write,users:read",
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await slack_oauth.exchange_code(
                client=client,
                client_id="cid",
                client_secret="csec",
                redirect_url="https://x/cb",
                code="abc",
            )
        assert result["team_id"] == "T123"
        assert result["team_name"] == "Sample Workspace"
        assert result["bot_user_id"] == "U_BOT"
        assert result["bot_access_token"] == "xoxb-test-1234"
        assert "chat:write" in result["scopes"]

    @pytest.mark.asyncio
    async def test_slack_not_ok_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": False, "error": "invalid_code"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(slack_oauth.OAuthExchangeError, match="invalid_code"):
                await slack_oauth.exchange_code(
                    client=client,
                    client_id="cid",
                    client_secret="csec",
                    redirect_url="https://x/cb",
                    code="abc",
                )

    @pytest.mark.asyncio
    async def test_missing_fields_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    # Missing access_token & bot_user_id
                    "team": {"id": "T1", "name": "x"},
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(slack_oauth.OAuthExchangeError, match="missing"):
                await slack_oauth.exchange_code(
                    client=client,
                    client_id="cid",
                    client_secret="csec",
                    redirect_url="https://x/cb",
                    code="abc",
                )
