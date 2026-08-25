from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture
def admin_auth(tenant_admin_token):
    return {"Authorization": f"Bearer {tenant_admin_token}"}


async def _register_device(client, admin_auth) -> str:
    r = await client.post(
        "/api/v1/devices/register", json={"platform": "macos"}, headers=admin_auth
    )
    return r.json()["device_id"]


@pytest.mark.asyncio
async def test_admin_creates_nudge_directive(client, admin_auth):
    device_id = await _register_device(client, admin_auth)
    resp = await client.post(
        f"/api/v1/devices/{device_id}/directives/nudge",
        json={
            "title": "Try ⌘K",
            "body": "Faster nav",
            "severity": "low",
            "coaching_tag": "shortcut",
        },
        headers=admin_auth,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "nudge" and body["origin"] == "admin"
    assert body["status"] == "pending"
    assert body["payload"]["title"] == "Try ⌘K"


@pytest.mark.asyncio
async def test_nudge_creation_requires_admin(client, admin_auth):
    device_id = await _register_device(client, admin_auth)
    resp = await client.post(
        f"/api/v1/devices/{device_id}/directives/nudge",
        json={"title": "t", "body": "b", "severity": "low", "coaching_tag": "shortcut"},
        headers={},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_nudge_creation_404_for_unknown_device(client, admin_auth):
    resp = await client.post(
        f"/api/v1/devices/{uuid.uuid4()}/directives/nudge",
        json={"title": "t", "body": "b", "severity": "low", "coaching_tag": "shortcut"},
        headers=admin_auth,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_device_polls_pending_then_marked_delivered(client, admin_auth):
    reg = await client.post(
        "/api/v1/devices/register", json={"platform": "macos"}, headers=admin_auth
    )
    device_id = reg.json()["device_id"]
    device_token = reg.json()["device_token"]

    await client.post(
        f"/api/v1/devices/{device_id}/directives/nudge",
        json={"title": "t", "body": "b", "severity": "low", "coaching_tag": "shortcut"},
        headers=admin_auth,
    )

    p1 = await client.get("/api/v1/devices/directives", headers={"X-Device-Token": device_token})
    assert p1.status_code == 200
    items = p1.json()
    assert len(items) == 1 and items[0]["status"] == "pending"

    p2 = await client.get("/api/v1/devices/directives", headers={"X-Device-Token": device_token})
    assert p2.json() == []


@pytest.mark.asyncio
async def test_poll_requires_device_token(client):
    r = await client.get("/api/v1/devices/directives", headers={"X-Device-Token": "psd_wrong"})
    assert r.status_code == 401
