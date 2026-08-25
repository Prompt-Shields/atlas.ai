"""Schema-shape tests for the MDM connect endpoints.

Pydantic-only tests — no DB, no httpx. Verifies the request bodies
reject bad input the way the API expects.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routers.mdm_connect import (
    JamfConnectRequest,
    JumpCloudConnectRequest,
    KandjiConnectRequest,
)

pytestmark = [pytest.mark.unit]


# ─── Jamf ────────────────────────────────────────────────────────────


class TestJamfConnectSchema:
    def test_happy_path(self) -> None:
        req = JamfConnectRequest(
            server_url="https://acme.jamfcloud.com",
            username="svc",
            password="hunter2",
        )
        assert str(req.server_url).startswith("https://acme.jamfcloud.com")
        assert req.username == "svc"

    def test_rejects_non_url(self) -> None:
        with pytest.raises(ValidationError):
            JamfConnectRequest(
                server_url="acme.jamfcloud.com",
                username="svc",
                password="pw",
            )

    def test_rejects_empty_password(self) -> None:
        with pytest.raises(ValidationError):
            JamfConnectRequest(
                server_url="https://acme.jamfcloud.com",
                username="svc",
                password="",
            )

    def test_rejects_empty_username(self) -> None:
        with pytest.raises(ValidationError):
            JamfConnectRequest(
                server_url="https://acme.jamfcloud.com",
                username="",
                password="pw",
            )


# ─── Kandji ──────────────────────────────────────────────────────────


class TestKandjiConnectSchema:
    def test_happy_path(self) -> None:
        req = KandjiConnectRequest(
            base_url="https://acme.api.kandji.io",
            api_token="kandji-token-1234567890",
        )
        assert str(req.base_url).startswith("https://acme.api.kandji.io")
        assert req.api_token.startswith("kandji-token")

    def test_short_token_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KandjiConnectRequest(
                base_url="https://acme.api.kandji.io",
                api_token="short",  # min_length=10
            )

    def test_non_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KandjiConnectRequest(
                base_url="acme.api.kandji.io",
                api_token="kandji-token-1234567890",
            )


# ─── JumpCloud ───────────────────────────────────────────────────────


class TestJumpCloudConnectSchema:
    def test_happy_path(self) -> None:
        req = JumpCloudConnectRequest(api_key="jc-key-12345678901234567890")
        assert req.api_key.startswith("jc-key")

    def test_short_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JumpCloudConnectRequest(api_key="short")
