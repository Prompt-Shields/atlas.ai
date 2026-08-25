"""Unit tests for auth API endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class TestHealthEndpoint:
    async def test_health(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"


class TestAuthEndpoints:
    async def test_login_missing_credentials(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    async def test_login_invalid_credentials(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@test.com", "password": "BadPassw0rd!"},
        )
        assert resp.status_code in (401, 404)

    async def test_protected_endpoint_no_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 401

    async def test_protected_endpoint_bad_token(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401
