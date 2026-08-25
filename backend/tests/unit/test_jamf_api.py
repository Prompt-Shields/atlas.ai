"""Unit tests for app.services.jamf_api.

Covers:
  - acquire_bearer_token happy path + 401 → JamfAuthError + non-2xx
  - iter_computers pagination (page + page-size + totalCount)
  - normalise_computer (full payload, missing identifier, derived
    compliance state)
  - normalise_group (smart vs static)
  - Basic-auth header encoding
"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.services.jamf_api import (
    JamfAPIError,
    JamfAuthError,
    acquire_bearer_token,
    iter_computers,
    iter_groups,
    normalise_computer,
    normalise_group,
)

pytestmark = [pytest.mark.unit]


# ─── normalise_computer ──────────────────────────────────────────────


class TestNormaliseComputer:
    def test_full_payload(self) -> None:
        raw = {
            "id": "42",
            "general": {
                "name": "  MBP-Alice  ",
                "lastInventoryUpdateDate": "2026-05-01T12:00:00Z",
                "managementId": "abc-def",
            },
            "hardware": {
                "osVersion": "14.5",
                "gatekeeperStatus": "ENABLED",
                "fileVault2Eligibility": "ELIGIBLE",
            },
            "userAndLocation": {
                "email": "Alice@Example.com",
                "username": "alice",
            },
        }
        out = normalise_computer(raw)
        assert out is not None
        assert out["external_device_id"] == "42"
        assert out["device_name"] == "MBP-Alice"
        assert out["operating_system"] == "macOS"
        assert out["os_version"] == "14.5"
        assert out["compliance_state"] == "compliant"
        assert out["enrolled_user_email"] == "alice@example.com"
        assert out["last_sync_to_mdm_at"] is not None

    def test_missing_id_returns_none(self) -> None:
        raw = {"general": {"name": "X"}, "hardware": {}, "userAndLocation": {}}
        assert normalise_computer(raw) is None

    def test_unmanaged_yields_unknown_compliance(self) -> None:
        raw = {
            "id": "1",
            "general": {"name": "X", "managementId": None, "mdmCapable": False},
            "hardware": {},
            "userAndLocation": {},
        }
        assert normalise_computer(raw)["compliance_state"] == "unknown"

    def test_managed_without_filevault_is_noncompliant(self) -> None:
        raw = {
            "id": "1",
            "general": {"name": "X", "managementId": "m"},
            "hardware": {"gatekeeperStatus": "ENABLED"},
            "userAndLocation": {},
        }
        # gatekeeper ok but no FileVault eligibility flag set → noncompliant
        assert normalise_computer(raw)["compliance_state"] == "noncompliant"

    def test_unnamed_device(self) -> None:
        raw = {"id": "1", "general": {}, "hardware": {}, "userAndLocation": {}}
        assert normalise_computer(raw)["device_name"] == "(unnamed)"


# ─── normalise_group ─────────────────────────────────────────────────


class TestNormaliseGroup:
    def test_smart_group(self) -> None:
        raw = {"id": "9", "name": "All Macs", "isSmart": True, "comments": "auto"}
        out = normalise_group(raw)
        assert out["external_group_id"] == "9"
        assert out["display_name"] == "All Macs"
        assert out["description"] == "auto"
        assert out["group_type"] == "Smart"

    def test_static_group(self) -> None:
        raw = {"id": "10", "name": "Pilot", "isSmart": False}
        assert normalise_group(raw)["group_type"] == "Static"

    def test_unnamed(self) -> None:
        raw = {"id": "1"}
        assert normalise_group(raw)["display_name"] == "(unnamed)"


# ─── acquire_bearer_token ────────────────────────────────────────────


class TestAcquireBearer:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        captured: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization", "")
            captured["path"] = request.url.path
            return httpx.Response(
                200,
                json={
                    "token": "bearer-xyz",
                    "expires": "2026-05-12T13:00:00Z",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await acquire_bearer_token(
                client,
                base_url="https://t.jamfcloud.com",
                username="svc",
                password="secret",
            )

        assert result["token"] == "bearer-xyz"
        assert captured["path"] == "/api/v1/auth/token"
        # Verify Basic auth header
        encoded = base64.b64encode(b"svc:secret").decode("ascii")
        assert captured["auth"] == f"Basic {encoded}"

    @pytest.mark.asyncio
    async def test_invalid_credentials_raises_auth_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"errors": [{"code": "INVALID_TOKEN", "description": "bad"}]}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JamfAuthError):
                await acquire_bearer_token(
                    client,
                    base_url="https://t.jamfcloud.com",
                    username="x",
                    password="y",
                )

    @pytest.mark.asyncio
    async def test_500_raises_generic(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JamfAPIError) as info:
                await acquire_bearer_token(
                    client,
                    base_url="https://t.jamfcloud.com",
                    username="x",
                    password="y",
                )
            assert info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_missing_token_field_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"something": "else"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JamfAPIError):
                await acquire_bearer_token(
                    client,
                    base_url="https://t.jamfcloud.com",
                    username="x",
                    password="y",
                )


# ─── Pagination ──────────────────────────────────────────────────────


class TestIterComputersPagination:
    @pytest.mark.asyncio
    async def test_walks_multiple_pages(self) -> None:
        responses = [
            {
                "results": [{"id": "1"}, {"id": "2"}],
                "totalCount": 3,
            },
            {
                "results": [{"id": "3"}],
                "totalCount": 3,
            },
        ]
        idx = {"i": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            body = responses[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [
                c
                async for c in iter_computers(
                    client,
                    base_url="https://t.jamfcloud.com",
                    token="t",
                    page_size=2,
                )
            ]
        assert [c["id"] for c in results] == ["1", "2", "3"]

    @pytest.mark.asyncio
    async def test_empty_inventory(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": [], "totalCount": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [
                c
                async for c in iter_computers(client, base_url="https://t.jamfcloud.com", token="t")
            ]
        assert results == []

    @pytest.mark.asyncio
    async def test_iter_groups_walks(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": "1", "name": "A", "isSmart": True},
                        {"id": "2", "name": "B", "isSmart": False},
                    ],
                    "totalCount": 2,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [
                g async for g in iter_groups(client, base_url="https://t.jamfcloud.com", token="t")
            ]
        assert [g["id"] for g in results] == ["1", "2"]
