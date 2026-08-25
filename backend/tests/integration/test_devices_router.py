"""Endpoint tests for /api/v1/devices (register + heartbeat)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture
def device_auth(tenant_admin_token):
    """Bearer-only auth: the widget authenticates with the user JWT (Auth0),
    which already encodes TEST_TENANT_ID. No X-API-Key mechanism exists."""
    return {"Authorization": f"Bearer {tenant_admin_token}"}


@pytest.mark.asyncio
async def test_register_mints_device_and_token(client, device_auth):
    resp = await client.post(
        "/api/v1/devices/register",
        json={"platform": "macos", "app_version": "1.0"},
        headers=device_auth,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"]
    assert body["device_token"].startswith("psd_")


@pytest.mark.asyncio
async def test_heartbeat_requires_valid_device_token(client, device_auth):
    reg = await client.post(
        "/api/v1/devices/register",
        json={"platform": "macos"},
        headers=device_auth,
    )
    token = reg.json()["device_token"]

    ok = await client.post("/api/v1/devices/heartbeat", headers={"X-Device-Token": token})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    bad = await client.post("/api/v1/devices/heartbeat", headers={"X-Device-Token": "psd_wrong"})
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_registration_requires_jwt(client):
    resp = await client.post(
        "/api/v1/devices/register",
        json={"platform": "macos"},
        headers={},
    )
    assert resp.status_code in (401, 403)
