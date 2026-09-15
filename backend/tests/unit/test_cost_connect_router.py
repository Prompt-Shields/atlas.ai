"""Tests for the pull-mode cost provider connect endpoints.

Three things here are worth more than schema coverage:

1. **The probe stores a bare key.** Every cost connector does a plain
   ``decrypt_token(integration.access_token_encrypted)`` and uses the
   result directly. ``mdm_connect`` stores a JSON blob instead, so the
   wrong convention here would produce integrations that connect fine
   and then fail in the sync worker. Asserted, not assumed.
2. **Failed verification persists nothing.** A rejected credential must
   leave no database row and no ciphertext behind.
3. **The Vercel AI Gateway key never lands in cleartext.**
   ``config_json`` is served to the browser as ``IntegrationCard.config``.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.models.integration import IntegrationProvider
from app.routers import cost_connect as cc
from app.services.crypto import decrypt_token
from app.services.integration_connect import redact_config

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _admin() -> SimpleNamespace:
    import uuid

    return SimpleNamespace(tenant_id=uuid.uuid4(), user_id=uuid.uuid4())


class _Raises:
    """Connector whose fetch_cost always raises the given exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def fetch_cost(self, integration: Any, since: date, until: date) -> list[Any]:
        raise self._exc


class _Records:
    """Connector that succeeds and records the window it was asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, date, date]] = []

    async def fetch_cost(self, integration: Any, since: date, until: date) -> list[Any]:
        self.calls.append((integration, since, until))
        return []


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://vendor.example/billing")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# ─── Request schemas ─────────────────────────────────────────────────


class TestRequestSchemas:
    def test_strips_pasted_whitespace(self) -> None:
        assert cc.AnthropicConnectRequest(api_key="  sk-ant-admin-abc123  ").api_key == (
            "sk-ant-admin-abc123"
        )

    @pytest.mark.parametrize(
        "model",
        [
            cc.AnthropicConnectRequest,
            cc.OpenAIConnectRequest,
            cc.CursorConnectRequest,
        ],
    )
    def test_rejects_short_key(self, model: type) -> None:
        with pytest.raises(ValidationError):
            model(api_key="short")

    def test_rejects_whitespace_only_key(self) -> None:
        with pytest.raises(ValidationError):
            cc.AnthropicConnectRequest(api_key="            ")

    def test_copilot_accepts_pasted_org_url(self) -> None:
        req = cc.CopilotConnectRequest(
            api_key="ghp_abcdefghijklmnop",
            github_org="https://github.com/promptshields/",
        )
        assert req.github_org == "promptshields"

    def test_copilot_rejects_zero_seat_price(self) -> None:
        with pytest.raises(ValidationError):
            cc.CopilotConnectRequest(
                api_key="ghp_abcdefghijklmnop",
                github_org="acme",
                seat_price_usd=0,
            )

    def test_copilot_seat_price_optional(self) -> None:
        req = cc.CopilotConnectRequest(api_key="ghp_abcdefghijklmnop", github_org="acme")
        assert req.seat_price_usd is None

    def test_vercel_gateway_defaults_off(self) -> None:
        req = cc.VercelConnectRequest(api_key="vercel_tok_abcdefgh")
        assert req.ai_gateway is False
        assert req.ai_gateway_key is None


# ─── The probe matches what connectors expect ────────────────────────


class TestProbeIsAdapterCompatible:
    def test_stores_bare_key_not_a_json_blob(self) -> None:
        """Every cost connector decrypts straight to the key string."""
        probe = cc._probe(
            provider=IntegrationProvider.ANTHROPIC,
            api_key="sk-ant-admin-xyz",
            config={},
        )
        assert decrypt_token(probe.access_token_encrypted) == "sk-ant-admin-xyz"

    def test_config_is_valid_json(self) -> None:
        probe = cc._probe(
            provider=IntegrationProvider.GITHUB_COPILOT,
            api_key="ghp_abcdefghijklmnop",
            config={"github_org": "acme"},
        )
        assert json.loads(probe.config_json) == {"github_org": "acme"}

    def test_probe_is_unsaved(self) -> None:
        probe = cc._probe(
            provider=IntegrationProvider.CURSOR,
            api_key="key_abcdefghijkl",
            config={},
        )
        assert probe.id is None


# ─── Verification error mapping ──────────────────────────────────────


class TestVerify:
    async def test_empty_result_is_a_pass(self) -> None:
        """A tenant with no spend yesterday is connected, not rejected."""
        connector = _Records()
        probe = cc._probe(provider=IntegrationProvider.OPENAI, api_key="sk-admin-abc", config={})
        await cc._verify(connector, probe, vendor="OpenAI")
        assert len(connector.calls) == 1

    async def test_verifies_over_a_one_day_window(self) -> None:
        connector = _Records()
        probe = cc._probe(provider=IntegrationProvider.OPENAI, api_key="sk-admin-abc", config={})
        await cc._verify(connector, probe, vendor="OpenAI")
        _, since, until = connector.calls[0]
        assert (until - since).days == 1

    @pytest.mark.parametrize("code", [401, 403])
    async def test_auth_failures_map_to_unauthorized(self, code: int) -> None:
        with pytest.raises(UnauthorizedError):
            await cc._verify(_Raises(_status_error(code)), object(), vendor="Anthropic")

    async def test_404_explains_the_org_is_wrong(self) -> None:
        with pytest.raises(ConflictError) as exc:
            await cc._verify(_Raises(_status_error(404)), object(), vendor="GitHub")
        assert "organization or team" in str(exc.value)

    @pytest.mark.parametrize("code", [429, 500, 503])
    async def test_other_status_codes_map_to_conflict(self, code: int) -> None:
        with pytest.raises(ConflictError):
            await cc._verify(_Raises(_status_error(code)), object(), vendor="Cursor")

    async def test_network_failure_maps_to_conflict(self) -> None:
        with pytest.raises(ConflictError) as exc:
            await cc._verify(_Raises(httpx.ConnectError("no route")), object(), vendor="Vercel")
        assert "Could not reach Vercel" in str(exc.value)

    async def test_unusable_payload_maps_to_conflict(self) -> None:
        """A connector ValueError (missing org, junk body) is a failed connect."""
        with pytest.raises(ConflictError):
            await cc._verify(
                _Raises(ValueError("config is missing 'github_org'")),
                object(),
                vendor="GitHub",
            )


# ─── Persistence only happens after verification ─────────────────────


class _ExplodingDB:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"database touched during a failed connect: .{name}")


class TestConnectPersistence:
    async def test_failed_verification_writes_nothing(self) -> None:
        with pytest.raises(UnauthorizedError):
            await cc._connect(
                _ExplodingDB(),
                _admin(),
                provider=IntegrationProvider.ANTHROPIC,
                connector=_Raises(_status_error(401)),
                vendor="Anthropic",
                api_key="sk-ant-admin-bad",
                display_name="Anthropic",
            )

    async def test_tenantless_user_is_forbidden(self) -> None:
        user = SimpleNamespace(tenant_id=None, user_id=None)
        with pytest.raises(ForbiddenError):
            await cc._connect(
                _ExplodingDB(),
                user,
                provider=IntegrationProvider.ANTHROPIC,
                connector=_Records(),
                vendor="Anthropic",
                api_key="sk-ant-admin-abc",
                display_name="Anthropic",
            )


# ─── Endpoint config assembly ────────────────────────────────────────


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub out verification and persistence; capture the upsert kwargs."""
    seen: dict[str, Any] = {}

    async def _no_verify(*_a: Any, **_k: Any) -> None:
        return None

    async def _fake_upsert(_db: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(id="fake")

    monkeypatch.setattr(cc, "_verify", _no_verify)
    monkeypatch.setattr(cc, "upsert_connected", _fake_upsert)
    monkeypatch.setattr(cc, "to_card", lambda record: record)
    return seen


class TestCopilotEndpoint:
    async def test_org_lands_in_config_and_external_id(self, captured: dict[str, Any]) -> None:
        await cc.github_copilot_connect(
            cc.CopilotConnectRequest(api_key="ghp_abcdefghijklmnop", github_org="acme"),
            _admin(),
            db=object(),
        )
        assert captured["config"] == {"github_org": "acme"}
        assert captured["external_id"] == "acme"
        assert captured["display_name"] == "GitHub Copilot (acme)"

    async def test_seat_price_stored_as_string(self, captured: dict[str, Any]) -> None:
        """Decimal via JSON float would reintroduce binary rounding."""
        await cc.github_copilot_connect(
            cc.CopilotConnectRequest(
                api_key="ghp_abcdefghijklmnop",
                github_org="acme",
                seat_price_usd="19.00",
            ),
            _admin(),
            db=object(),
        )
        assert captured["config"]["copilot_seat_price_usd"] == "19.00"
        assert isinstance(captured["config"]["copilot_seat_price_usd"], str)


class TestVercelEndpointSecretHandling:
    async def test_gateway_key_is_encrypted_not_cleartext(self, captured: dict[str, Any]) -> None:
        await cc.vercel_connect(
            cc.VercelConnectRequest(
                api_key="vercel_tok_abcdefgh",
                team_slug="acme",
                ai_gateway=True,
                ai_gateway_key="gw_supersecret_value",
            ),
            _admin(),
            db=object(),
        )
        config = captured["config"]
        assert "vercel_ai_gateway_key" not in config
        stored = config["vercel_ai_gateway_key_encrypted"]
        assert stored != "gw_supersecret_value"
        # The adapter reads it back with decrypt_token — same contract.
        assert decrypt_token(stored) == "gw_supersecret_value"

    async def test_encrypted_key_is_stripped_from_the_api_response(
        self, captured: dict[str, Any]
    ) -> None:
        await cc.vercel_connect(
            cc.VercelConnectRequest(
                api_key="vercel_tok_abcdefgh",
                ai_gateway=True,
                ai_gateway_key="gw_supersecret_value",
            ),
            _admin(),
            db=object(),
        )
        visible = redact_config(json.dumps(captured["config"]))
        assert "vercel_ai_gateway_key_encrypted" not in visible
        assert visible == {"vercel_ai_gateway": True}

    async def test_no_gateway_key_stored_when_gateway_disabled(
        self, captured: dict[str, Any]
    ) -> None:
        await cc.vercel_connect(
            cc.VercelConnectRequest(
                api_key="vercel_tok_abcdefgh",
                ai_gateway=False,
                ai_gateway_key="gw_ignored",
            ),
            _admin(),
            db=object(),
        )
        assert captured["config"] == {}


# ─── redact_config ───────────────────────────────────────────────────


class TestRedactConfig:
    def test_strips_encrypted_suffix_keys(self) -> None:
        raw = json.dumps({"github_org": "acme", "token_encrypted": "gAAAA..."})
        assert redact_config(raw) == {"github_org": "acme"}

    def test_malformed_json_yields_empty_dict(self) -> None:
        assert redact_config("{not json") == {}

    def test_non_object_yields_empty_dict(self) -> None:
        assert redact_config(json.dumps([1, 2, 3])) == {}

    def test_none_yields_empty_dict(self) -> None:
        assert redact_config(None) == {}


# ─── The Vercel adapter reads the encrypted key back ──────────────────


class TestVercelAdapterDecryptsGatewayKey:
    """Covers the one line of ``fetch_cost`` the connect flow changed.

    ``test_vercel_cost.py`` deliberately leaves ``fetch_cost``'s HTTP path
    alone, but this is the line that decides whether a credential is sent
    correctly, so it gets a test here rather than none at all.
    """

    async def test_gateway_request_carries_the_decrypted_key(self, httpx_mock: Any) -> None:
        from app.models.integration import Integration
        from app.services.cost.vercel_cost import VercelCostConnector
        from app.services.crypto import encrypt_token

        # Responses are consumed in registration order: the connector
        # calls billing first, then the gateway.
        httpx_mock.add_response(text="")
        httpx_mock.add_response(json={"data": []})

        integration = Integration(
            provider=IntegrationProvider.VERCEL,
            access_token_encrypted=encrypt_token("main_access_token"),
            config_json=json.dumps(
                {
                    "vercel_ai_gateway": True,
                    "vercel_ai_gateway_key_encrypted": encrypt_token("gw_real_secret"),
                }
            ),
        )
        await VercelCostConnector().fetch_cost(integration, date(2026, 9, 1), date(2026, 9, 2))

        gateway_request = next(r for r in httpx_mock.get_requests() if "ai-gateway" in str(r.url))
        assert gateway_request.headers["Authorization"] == "Bearer gw_real_secret"

    async def test_falls_back_to_the_main_token_when_no_gateway_key(self, httpx_mock: Any) -> None:
        from app.models.integration import Integration
        from app.services.cost.vercel_cost import VercelCostConnector
        from app.services.crypto import encrypt_token

        # Responses are consumed in registration order: the connector
        # calls billing first, then the gateway.
        httpx_mock.add_response(text="")
        httpx_mock.add_response(json={"data": []})

        integration = Integration(
            provider=IntegrationProvider.VERCEL,
            access_token_encrypted=encrypt_token("main_access_token"),
            config_json=json.dumps({"vercel_ai_gateway": True}),
        )
        await VercelCostConnector().fetch_cost(integration, date(2026, 9, 1), date(2026, 9, 2))

        gateway_request = next(r for r in httpx_mock.get_requests() if "ai-gateway" in str(r.url))
        assert gateway_request.headers["Authorization"] == "Bearer main_access_token"

    def test_non_string_input_yields_empty_dict(self) -> None:
        assert redact_config(object()) == {}  # type: ignore[arg-type]
