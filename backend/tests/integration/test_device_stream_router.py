from __future__ import annotations

import uuid

import pytest

from app.services.device_directive_sse import device_directive_notifier

pytestmark = [pytest.mark.integration]


@pytest.fixture
def admin_auth(tenant_admin_token):
    return {"Authorization": f"Bearer {tenant_admin_token}"}


@pytest.mark.asyncio
async def test_creating_a_nudge_publishes_directive_available(client, admin_auth):
    reg = await client.post(
        "/api/v1/devices/register", json={"platform": "macos"}, headers=admin_auth
    )
    device_id = uuid.UUID(reg.json()["device_id"])

    queue = await device_directive_notifier.subscribe(device_id)
    try:
        await client.post(
            f"/api/v1/devices/{device_id}/directives/nudge",
            json={"title": "t", "body": "b", "severity": "low", "coaching_tag": "shortcut"},
            headers=admin_auth,
        )
        event = queue.get_nowait()
        assert event["event"] == "directive_available"
    finally:
        await device_directive_notifier.unsubscribe(device_id, queue)


class _FakeRedis:
    _store: dict = {}

    async def set(self, k, v, ex=None):
        _FakeRedis._store[k] = v

    async def get(self, k):
        return _FakeRedis._store.get(k)

    async def delete(self, k):
        _FakeRedis._store.pop(k, None)

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    import redis.asyncio as redis_asyncio

    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: _FakeRedis())
    _FakeRedis._store.clear()
    return _FakeRedis


@pytest.mark.asyncio
async def test_device_stream_ticket_requires_device_token(client, admin_auth, fake_redis):
    reg = await client.post(
        "/api/v1/devices/register", json={"platform": "macos"}, headers=admin_auth
    )
    token = reg.json()["device_token"]
    ok = await client.post("/api/v1/devices/stream/ticket", headers={"X-Device-Token": token})
    assert ok.status_code == 200
    assert ok.json()["ticket"]
    bad = await client.post(
        "/api/v1/devices/stream/ticket", headers={"X-Device-Token": "psd_wrong"}
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_device_stream_rejects_bad_ticket(client, fake_redis):
    r = await client.get("/api/v1/devices/stream?ticket=nope")
    assert r.status_code == 401
