from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture
def admin_auth(tenant_admin_token):
    return {"Authorization": f"Bearer {tenant_admin_token}"}


async def _register_and_nudge(client, admin_auth):
    reg = await client.post(
        "/api/v1/devices/register", json={"platform": "macos"}, headers=admin_auth
    )
    device_id = reg.json()["device_id"]
    token = reg.json()["device_token"]
    n = await client.post(
        f"/api/v1/devices/{device_id}/directives/nudge",
        json={"title": "t", "body": "b", "severity": "low", "coaching_tag": "shortcut"},
        headers=admin_auth,
    )
    return device_id, token, n.json()["id"]


@pytest.mark.asyncio
async def test_ack_shown_sets_acknowledged(client, admin_auth):
    _, token, directive_id = await _register_and_nudge(client, admin_auth)
    r = await client.post(
        f"/api/v1/devices/directives/{directive_id}/ack",
        json={"outcome": "shown"},
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "acknowledged"


@pytest.mark.asyncio
async def test_ack_accepted_sets_applied(client, admin_auth):
    _, token, directive_id = await _register_and_nudge(client, admin_auth)
    r = await client.post(
        f"/api/v1/devices/directives/{directive_id}/ack",
        json={"outcome": "accepted"},
        headers={"X-Device-Token": token},
    )
    assert r.json()["status"] == "applied"


@pytest.mark.asyncio
async def test_ack_rejects_cross_device_directive(client, admin_auth):
    _, _, directive_id = await _register_and_nudge(client, admin_auth)
    regB = await client.post(
        "/api/v1/devices/register", json={"platform": "macos"}, headers=admin_auth
    )
    tokenB = regB.json()["device_token"]
    r = await client.post(
        f"/api/v1/devices/directives/{directive_id}/ack",
        json={"outcome": "shown"},
        headers={"X-Device-Token": tokenB},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ack_requires_device_token(client, admin_auth):
    _, _, directive_id = await _register_and_nudge(client, admin_auth)
    r = await client.post(
        f"/api/v1/devices/directives/{directive_id}/ack",
        json={"outcome": "shown"},
        headers={"X-Device-Token": "psd_wrong"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_ack_unknown_directive_404(client, admin_auth):
    _, token, _ = await _register_and_nudge(client, admin_auth)
    r = await client.post(
        f"/api/v1/devices/directives/{uuid.uuid4()}/ack",
        json={"outcome": "shown"},
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 404
