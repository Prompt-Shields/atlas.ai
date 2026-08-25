"""Unit tests for app.services.kandji_api."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.services.kandji_api import (
    KandjiAPIError,
    KandjiAuthError,
    iter_devices,
    normalise_blueprint,
    normalise_device,
)

pytestmark = [pytest.mark.unit]


# ─── normalise_device ────────────────────────────────────────────────


class TestNormaliseDevice:
    def test_full_payload(self) -> None:
        # Last check-in within 7 days so compliance is 'compliant'.
        recent = (datetime.now(UTC) - timedelta(hours=4)).isoformat()
        raw = {
            "device_id": "abc-123",
            "device_name": "MBP-Alice",
            "platform": "Mac",
            "os_version": "14.5",
            "user": {"email": "Alice@Example.com"},
            "last_check_in": recent,
            "mdm_enabled": True,
        }
        out = normalise_device(raw)
        assert out is not None
        assert out["external_device_id"] == "abc-123"
        assert out["device_name"] == "MBP-Alice"
        assert out["operating_system"] == "macOS"
        assert out["os_version"] == "14.5"
        assert out["compliance_state"] == "compliant"
        assert out["enrolled_user_email"] == "alice@example.com"
        assert out["last_sync_to_mdm_at"] is not None

    def test_missing_id_returns_none(self) -> None:
        assert normalise_device({"device_name": "X"}) is None

    def test_iphone_platform(self) -> None:
        raw = {
            "device_id": "1",
            "device_name": "Bob iPhone",
            "platform": "iPhone",
            "last_check_in": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "mdm_enabled": True,
        }
        assert normalise_device(raw)["operating_system"] == "iOS"

    def test_no_mdm_returns_unknown_compliance(self) -> None:
        raw = {
            "device_id": "1",
            "device_name": "X",
            "platform": "Mac",
            "mdm_enabled": False,
        }
        assert normalise_device(raw)["compliance_state"] == "unknown"

    def test_stale_check_in_is_noncompliant(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        raw = {
            "device_id": "1",
            "device_name": "X",
            "platform": "Mac",
            "mdm_enabled": True,
            "last_check_in": old,
        }
        assert normalise_device(raw)["compliance_state"] == "noncompliant"

    def test_unnamed_device(self) -> None:
        raw = {"device_id": "1", "platform": "Mac"}
        assert normalise_device(raw)["device_name"] == "(unnamed)"


# ─── normalise_blueprint ─────────────────────────────────────────────


class TestNormaliseBlueprint:
    def test_full(self) -> None:
        raw = {"id": "bp-1", "name": "All Macs", "description": "default"}
        out = normalise_blueprint(raw)
        assert out["external_group_id"] == "bp-1"
        assert out["display_name"] == "All Macs"
        assert out["description"] == "default"
        assert out["group_type"] == "Blueprint"


# ─── iter_devices pagination ─────────────────────────────────────────


class TestIterDevicesPagination:
    @pytest.mark.asyncio
    async def test_walks_multiple_pages(self) -> None:
        """Kandji returns bare arrays. Stop when page < limit."""
        responses = [
            [{"device_id": str(i)} for i in range(300)],  # full page
            [{"device_id": "300"}, {"device_id": "301"}],  # tail
        ]
        idx = {"i": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            body = responses[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            count = 0
            async for _ in iter_devices(
                client,
                base_url="https://t.api.kandji.io",
                api_token="t",
                page_size=300,
            ):
                count += 1
        assert count == 302

    @pytest.mark.asyncio
    async def test_empty_tenant(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [
                d
                async for d in iter_devices(
                    client, base_url="https://t.api.kandji.io", api_token="t"
                )
            ]
        assert results == []


# ─── Errors ──────────────────────────────────────────────────────────


class TestErrors:
    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Invalid token"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(KandjiAuthError):
                async for _ in iter_devices(
                    client, base_url="https://t.api.kandji.io", api_token="bad"
                ):
                    pass

    @pytest.mark.asyncio
    async def test_403_raises_auth_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": "Forbidden"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(KandjiAuthError):
                async for _ in iter_devices(
                    client, base_url="https://t.api.kandji.io", api_token="x"
                ):
                    pass

    @pytest.mark.asyncio
    async def test_500_raises_api_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(KandjiAPIError) as info:
                async for _ in iter_devices(
                    client, base_url="https://t.api.kandji.io", api_token="t"
                ):
                    pass
            assert info.value.status_code == 500
