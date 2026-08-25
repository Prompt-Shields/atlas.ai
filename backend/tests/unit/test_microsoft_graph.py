"""Unit tests for app.services.microsoft_graph.

Pure-function tests + httpx.MockTransport for the API client.

Covers:
  - graph_get happy path + error mapping (401 → GraphAuthExpired,
    429 → GraphRateLimited, other → GraphAPIError)
  - iter_users pagination via @odata.nextLink
  - normalise_user / normalise_group field extraction
  - Lowercase normalisation of email
"""

from __future__ import annotations

import httpx
import pytest

from app.services.microsoft_graph import (
    GraphAPIError,
    GraphAuthExpired,
    GraphRateLimited,
    graph_get,
    iter_groups,
    iter_users,
    normalise_group,
    normalise_user,
)

pytestmark = [pytest.mark.unit]


# ─── normalise_user ──────────────────────────────────────────────────


class TestNormaliseUser:
    def test_full_payload(self) -> None:
        raw = {
            "id": "user-aad-1",
            "userPrincipalName": "Alice@Example.com",
            "mail": "Alice@Example.com",
            "displayName": "Alice Park  ",
            "department": " Grants Management ",
            "jobTitle": "Senior Officer",
            "accountEnabled": True,
        }
        out = normalise_user(raw)
        assert out == {
            "external_user_id": "user-aad-1",
            "email": "alice@example.com",
            "display_name": "Alice Park",
            "department": "Grants Management",
            "job_title": "Senior Officer",
            "manager_external_user_id": None,
            "is_active": True,
        }

    def test_falls_back_to_upn_when_mail_missing(self) -> None:
        raw = {
            "id": "u1",
            "userPrincipalName": "Bob@Example.com",
            "mail": None,
            "displayName": "Bob",
        }
        assert normalise_user(raw)["email"] == "bob@example.com"

    def test_account_disabled(self) -> None:
        raw = {
            "id": "u1",
            "userPrincipalName": "x@x.com",
            "displayName": "X",
            "accountEnabled": False,
        }
        assert normalise_user(raw)["is_active"] is False

    def test_missing_department(self) -> None:
        raw = {
            "id": "u1",
            "userPrincipalName": "x@x.com",
            "displayName": "X",
        }
        out = normalise_user(raw)
        assert out["department"] is None
        assert out["job_title"] is None

    def test_empty_string_fields_become_none(self) -> None:
        raw = {
            "id": "u1",
            "userPrincipalName": "x@x.com",
            "displayName": "X",
            "department": "   ",
            "jobTitle": "",
        }
        out = normalise_user(raw)
        assert out["department"] is None
        assert out["job_title"] is None


# ─── normalise_group ─────────────────────────────────────────────────


class TestNormaliseGroup:
    def test_security_group(self) -> None:
        raw = {
            "id": "g1",
            "displayName": "All Staff",
            "description": "Everyone",
            "groupTypes": [],
        }
        out = normalise_group(raw)
        assert out["external_group_id"] == "g1"
        assert out["display_name"] == "All Staff"
        assert out["group_type"] == "Security"

    def test_unified_m365_group(self) -> None:
        raw = {
            "id": "g2",
            "displayName": "Grants Team",
            "groupTypes": ["Unified"],
        }
        assert normalise_group(raw)["group_type"] == "Microsoft365"

    def test_distribution_group(self) -> None:
        raw = {
            "id": "g3",
            "displayName": "Newsletters",
            "groupTypes": ["DynamicMembership"],
        }
        assert normalise_group(raw)["group_type"] == "Distribution"


# ─── graph_get error mapping ─────────────────────────────────────────


class TestGraphGetErrors:
    @pytest.mark.asyncio
    async def test_200_returns_json(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer test-token"
            return httpx.Response(200, json={"value": [{"id": "x"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            body = await graph_get(client, token="test-token", path="/users")
            assert body == {"value": [{"id": "x"}]}

    @pytest.mark.asyncio
    async def test_401_raises_auth_expired(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={
                    "error": {
                        "code": "InvalidAuthenticationToken",
                        "message": "Lifetime validation failed",
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GraphAuthExpired):
                await graph_get(client, token="t", path="/users")

    @pytest.mark.asyncio
    async def test_429_raises_rate_limited_with_retry_after(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={"error": {"code": "throttled", "message": "Too many"}},
                headers={"Retry-After": "120"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GraphRateLimited) as info:
                await graph_get(client, token="t", path="/users")
            assert info.value.retry_after_seconds == 120

    @pytest.mark.asyncio
    async def test_500_raises_generic(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"code": "wat", "message": "x"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GraphAPIError) as info:
                await graph_get(client, token="t", path="/users")
            assert info.value.status_code == 500
            assert info.value.code == "wat"


# ─── iter_users pagination ───────────────────────────────────────────


class TestIterUsers:
    @pytest.mark.asyncio
    async def test_two_pages(self) -> None:
        """Verify @odata.nextLink walking yields all rows in order."""

        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "skiptoken=abc" in url:
                return httpx.Response(200, json={"value": [{"id": "u3"}]})
            # First page — matches /users without a skiptoken.
            if url.endswith("/users") or ("/users?" in url and "skiptoken" not in url):
                return httpx.Response(
                    200,
                    json={
                        "value": [{"id": "u1"}, {"id": "u2"}],
                        "@odata.nextLink": (
                            "https://graph.microsoft.com/v1.0/users?$skiptoken=abc"
                        ),
                    },
                )
            return httpx.Response(404, json={"error": {"code": "x", "message": "y"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [u async for u in iter_users(client, token="t")]
        assert [u["id"] for u in results] == ["u1", "u2", "u3"]

    @pytest.mark.asyncio
    async def test_single_page(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"value": [{"id": "lone"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [u async for u in iter_users(client, token="t")]
        assert [u["id"] for u in results] == ["lone"]

    @pytest.mark.asyncio
    async def test_empty_page(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"value": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [u async for u in iter_users(client, token="t")]
        assert results == []


# ─── iter_groups ─────────────────────────────────────────────────────


class TestIterGroups:
    @pytest.mark.asyncio
    async def test_walks(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "g1", "displayName": "All", "groupTypes": []},
                        {"id": "g2", "displayName": "Grants", "groupTypes": ["Unified"]},
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = [g async for g in iter_groups(client, token="t")]
        assert {g["id"] for g in results} == {"g1", "g2"}
