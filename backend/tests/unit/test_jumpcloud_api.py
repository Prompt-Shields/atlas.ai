"""Unit tests for app.services.jumpcloud_api."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.services.jumpcloud_api import (
    JumpCloudAPIError,
    JumpCloudAuthError,
    iter_systems,
    normalise_system,
    normalise_system_group,
)

pytestmark = [pytest.mark.unit]


# ─── normalise_system ────────────────────────────────────────────────


class TestNormaliseSystem:
    def test_darwin_full_payload(self) -> None:
        recent = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        raw = {
            "_id": "sys-1",
            "displayName": "MBP-Eng-01",
            "hostname": "mbp-eng-01.local",
            "systemType": "darwin",
            "os": "Mac OS X",
            "version": "14.5",
            "active": True,
            "lastContact": recent,
        }
        out = normalise_system(raw)
        assert out is not None
        assert out["external_device_id"] == "sys-1"
        assert out["device_name"] == "MBP-Eng-01"
        assert out["operating_system"] == "macOS"
        assert out["os_version"] == "14.5"
        assert out["compliance_state"] == "compliant"

    def test_windows(self) -> None:
        raw = {
            "_id": "sys-2",
            "displayName": "WIN-001",
            "systemType": "windows",
            "version": "11.0.22631",
            "active": True,
            "lastContact": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        }
        assert normalise_system(raw)["operating_system"] == "Windows"

    def test_linux(self) -> None:
        raw = {
            "_id": "sys-3",
            "displayName": "UBUNTU-01",
            "systemType": "linux",
            "version": "22.04",
            "active": True,
            "lastContact": (datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
        }
        assert normalise_system(raw)["operating_system"] == "Linux"

    def test_inactive_is_noncompliant(self) -> None:
        raw = {"_id": "1", "displayName": "X", "systemType": "darwin", "active": False}
        assert normalise_system(raw)["compliance_state"] == "noncompliant"

    def test_missing_active_is_unknown(self) -> None:
        raw = {"_id": "1", "displayName": "X", "systemType": "darwin"}
        assert normalise_system(raw)["compliance_state"] == "unknown"

    def test_stale_contact_is_noncompliant(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        raw = {
            "_id": "1",
            "displayName": "X",
            "systemType": "darwin",
            "active": True,
            "lastContact": old,
        }
        assert normalise_system(raw)["compliance_state"] == "noncompliant"

    def test_missing_id_returns_none(self) -> None:
        assert normalise_system({"displayName": "X"}) is None

    def test_falls_back_to_hostname(self) -> None:
        raw = {"_id": "1", "hostname": "fallback.local", "systemType": "darwin"}
        assert normalise_system(raw)["device_name"] == "fallback.local"

    def test_no_user_email(self) -> None:
        raw = {"_id": "1", "displayName": "X", "systemType": "darwin"}
        # JumpCloud doesn't carry the user email on the system row.
        assert normalise_system(raw)["enrolled_user_email"] is None


# ─── normalise_system_group ──────────────────────────────────────────


class TestNormaliseSystemGroup:
    def test_full(self) -> None:
        raw = {"id": "g-1", "name": "Engineering", "type": "system_group"}
        out = normalise_system_group(raw)
        assert out["external_group_id"] == "g-1"
        assert out["display_name"] == "Engineering"
        assert out["group_type"] == "system_group"

    def test_unnamed(self) -> None:
        assert normalise_system_group({"id": "1"})["display_name"] == "(unnamed)"


# ─── iter_systems pagination ─────────────────────────────────────────


class TestIterSystemsPagination:
    @pytest.mark.asyncio
    async def test_walks_multi_page(self) -> None:
        responses = [
            {
                "results": [{"_id": "a"}, {"_id": "b"}, {"_id": "c"}],
                "totalCount": 5,
            },
            {
                "results": [{"_id": "d"}, {"_id": "e"}],
                "totalCount": 5,
            },
        ]
        idx = {"i": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured_key = request.headers.get("x-api-key", "")
            assert captured_key == "secret"
            body = responses[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ids = [s["_id"] async for s in iter_systems(client, api_key="secret", page_size=3)]
        assert ids == ["a", "b", "c", "d", "e"]

    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": [], "totalCount": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert [s async for s in iter_systems(client, api_key="t")] == []


# ─── Errors ──────────────────────────────────────────────────────────


class TestErrors:
    @pytest.mark.asyncio
    async def test_401(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "bad key"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JumpCloudAuthError):
                async for _ in iter_systems(client, api_key="bad"):
                    pass

    @pytest.mark.asyncio
    async def test_403(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "forbidden"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JumpCloudAuthError):
                async for _ in iter_systems(client, api_key="t"):
                    pass

    @pytest.mark.asyncio
    async def test_500(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(JumpCloudAPIError) as info:
                async for _ in iter_systems(client, api_key="t"):
                    pass
            assert info.value.status_code == 500
