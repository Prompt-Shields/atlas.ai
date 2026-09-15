"""/api/v1/integrations/sentinel — forwarder config, status, dead letters.

Covers what the connect wizard and the admin dashboard depend on: that Azure
coordinates are stored all-or-nothing, that the client secret never comes back
out, and that the dead-letter backlog is visible and replayable.

The seeded-preview behaviour lives in `test_sentinel_connect_router.py`; this
file is about the live-forwarding surface added on top of it.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select

from app.models.integration import Integration, IntegrationProvider
from app.models.sentinel_forward import (
    SentinelDeadLetter,
    SentinelDeadLetterStatus,
    SentinelForwardCursor,
)
from app.services import crypto
from tests.conftest import (
    TEST_TENANT_ID,
    TestSessionLocal,
    auth_header,
    ensure_tenant,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/integrations/sentinel"
DCE = "https://acme-dce-abcd.eastus-1.ingest.monitor.azure.com"
DCR = "dcr-0123456789abcdef"


def connect_payload(**overrides) -> dict:
    payload = {
        "workspace_name": "Acme SOC",
        "table_name": "PromptShieldsActivity_CL",
        "enabled_event_types": ["Redacted", "Blocked"],
        "azure_tenant_id": "11111111-2222-3333-4444-555555555555",
        "client_id": "66666666-7777-8888-9999-000000000000",
        "client_secret": "super-secret",
        "dce_url": DCE,
        "dcr_immutable_id": DCR,
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto.reset_cache_for_tests()
    yield
    crypto.reset_cache_for_tests()


@pytest_asyncio.fixture
async def tenant(setup_database) -> None:
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID, name="Test Tenant")


async def stored_integration() -> Integration | None:
    async with TestSessionLocal() as session:
        return (
            await session.execute(
                select(Integration).where(
                    Integration.tenant_id == TEST_TENANT_ID,
                    Integration.provider == IntegrationProvider.SENTINEL,
                )
            )
        ).scalar_one_or_none()


class TestConnectWithForwarderConfig:
    async def test_stores_the_azure_coordinates(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        resp = await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        config = resp.json()["config"]
        assert config["dcr_immutable_id"] == DCR
        assert config["dce_url"] == DCE
        assert config["stream_name"] == "Custom-PromptShields_v1"

    async def test_never_returns_the_client_secret(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        resp = await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        assert "super-secret" not in resp.text

    async def test_stores_the_secret_encrypted(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        record = await stored_integration()
        assert record is not None
        assert record.refresh_token_encrypted != "super-secret"
        assert crypto.decrypt_token(record.refresh_token_encrypted) == "super-secret"

    async def test_reconnecting_invalidates_the_cached_bearer_token(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        # New app registration means any token minted for the old one is void.
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        async with TestSessionLocal() as session:
            record = (
                await session.execute(
                    select(Integration).where(Integration.tenant_id == TEST_TENANT_ID)
                )
            ).scalar_one()
            record.access_token_encrypted = crypto.encrypt_token("old-token")
            await session.commit()

        await client.post(
            f"{BASE}/connect",
            json=connect_payload(client_secret="rotated-secret"),
            headers=auth_header(tenant_admin_token),
        )
        record = await stored_integration()
        assert record is not None
        assert record.access_token_encrypted is None

    async def test_preview_only_connect_still_works(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        # The pre-forwarder wizard sent only these fields; it must keep working.
        resp = await client.post(
            f"{BASE}/connect",
            json={"workspace_name": "Acme SOC"},
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        events = await client.get(f"{BASE}/events", headers=auth_header(tenant_admin_token))
        assert events.json()["forwarder_configured"] is False

    async def test_events_reports_the_forwarder_as_configured(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        events = await client.get(f"{BASE}/events", headers=auth_header(tenant_admin_token))
        assert events.json()["forwarder_configured"] is True

    @pytest.mark.parametrize(
        "missing", ["azure_tenant_id", "client_id", "client_secret", "dce_url", "dcr_immutable_id"]
    )
    async def test_partial_azure_config_is_rejected(
        self, client: AsyncClient, tenant_admin_token: str, tenant, missing
    ) -> None:
        # A half-filled config would read as connected while forwarding nothing.
        resp = await client.post(
            f"{BASE}/connect",
            json=connect_payload(**{missing: None}),
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 422

    async def test_plaintext_dce_url_is_rejected(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        # The bearer token rides this request.
        resp = await client.post(
            f"{BASE}/connect",
            json=connect_payload(dce_url="http://insecure.example.com"),
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 422


class TestStatus:
    async def test_requires_auth(self, client: AsyncClient) -> None:
        assert (await client.get(f"{BASE}/status")).status_code == 401

    async def test_reports_not_connected_before_connect(
        self, client: AsyncClient, viewer_token: str, tenant
    ) -> None:
        body = (await client.get(f"{BASE}/status", headers=auth_header(viewer_token))).json()
        assert body["connected"] is False
        assert body["forwarder_configured"] is False

    async def test_reports_configuration_and_counters(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        body = (await client.get(f"{BASE}/status", headers=auth_header(tenant_admin_token))).json()
        assert body["connected"] is True
        assert body["forwarder_configured"] is True
        assert body["dcr_immutable_id"] == DCR
        assert body["enabled_event_types"] == ["Redacted", "Blocked"]
        assert body["events_forwarded"] == 0
        assert body["pending_dead_letters"] == 0

    async def test_surfaces_the_dead_letter_backlog(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        record = await stored_integration()
        assert record is not None
        async with TestSessionLocal() as session:
            session.add(
                SentinelForwardCursor(
                    tenant_id=TEST_TENANT_ID,
                    integration_id=record.id,
                    events_forwarded=12,
                    batches_dead_lettered=1,
                )
            )
            session.add(
                SentinelDeadLetter(
                    tenant_id=TEST_TENANT_ID,
                    integration_id=record.id,
                    reason="http_403",
                    event_count=4,
                    payload=[],
                )
            )
            await session.commit()

        body = (await client.get(f"{BASE}/status", headers=auth_header(tenant_admin_token))).json()
        assert body["events_forwarded"] == 12
        assert body["pending_dead_letters"] == 1


class TestForwardNow:
    async def test_requires_org_admin(self, client: AsyncClient, viewer_token: str, tenant) -> None:
        resp = await client.post(f"{BASE}/forward", headers=auth_header(viewer_token))
        assert resp.status_code == 403

    async def test_404_when_sentinel_is_not_connected(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        resp = await client.post(f"{BASE}/forward", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 404

    async def test_409_when_only_the_preview_is_configured(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json={"workspace_name": "Acme SOC"},
            headers=auth_header(tenant_admin_token),
        )
        resp = await client.post(f"{BASE}/forward", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 409
        assert "preview" in resp.json()["error"]["message"].lower()


class TestDeadLetters:
    async def test_requires_auth(self, client: AsyncClient) -> None:
        assert (await client.get(f"{BASE}/dead-letters")).status_code == 401

    async def test_empty_before_any_failure(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        body = (
            await client.get(f"{BASE}/dead-letters", headers=auth_header(tenant_admin_token))
        ).json()
        assert body == {"items": [], "total": 0}

    async def test_lists_batches_without_their_payloads(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        record = await stored_integration()
        assert record is not None
        async with TestSessionLocal() as session:
            session.add(
                SentinelDeadLetter(
                    tenant_id=TEST_TENANT_ID,
                    integration_id=record.id,
                    reason="exhausted_retries",
                    http_status=503,
                    error_detail="service unavailable",
                    event_count=2,
                    first_event_id="EV-1",
                    last_event_id="EV-2",
                    payload=[{"EventId": "EV-1", "PromptHash": "x" * 64}],
                )
            )
            await session.commit()

        body = (
            await client.get(f"{BASE}/dead-letters", headers=auth_header(tenant_admin_token))
        ).json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["reason"] == "exhausted_retries"
        assert item["event_count"] == 2
        assert "payload" not in item

    async def test_filters_by_status(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        record = await stored_integration()
        assert record is not None
        async with TestSessionLocal() as session:
            for status in (
                SentinelDeadLetterStatus.PENDING,
                SentinelDeadLetterStatus.REPLAYED,
            ):
                session.add(
                    SentinelDeadLetter(
                        tenant_id=TEST_TENANT_ID,
                        integration_id=record.id,
                        status=status,
                        reason="http_403",
                        event_count=1,
                        payload=[],
                    )
                )
            await session.commit()

        body = (
            await client.get(
                f"{BASE}/dead-letters?status=PENDING",
                headers=auth_header(tenant_admin_token),
            )
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "PENDING"


class TestReplayEndpoint:
    async def test_requires_org_admin(self, client: AsyncClient, viewer_token: str, tenant) -> None:
        resp = await client.post(
            f"{BASE}/dead-letters/{uuid.uuid4()}/replay", headers=auth_header(viewer_token)
        )
        assert resp.status_code == 403

    async def test_404_for_an_unknown_dead_letter(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        resp = await client.post(
            f"{BASE}/dead-letters/{uuid.uuid4()}/replay",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 404

    async def test_an_already_replayed_batch_is_not_sent_again(
        self, client: AsyncClient, tenant_admin_token: str, tenant
    ) -> None:
        # A second delivery would double the rows in the customer's table.
        await client.post(
            f"{BASE}/connect",
            json=connect_payload(),
            headers=auth_header(tenant_admin_token),
        )
        record = await stored_integration()
        assert record is not None
        async with TestSessionLocal() as session:
            letter = SentinelDeadLetter(
                tenant_id=TEST_TENANT_ID,
                integration_id=record.id,
                status=SentinelDeadLetterStatus.REPLAYED,
                reason="http_503",
                event_count=1,
                payload=[],
            )
            session.add(letter)
            await session.commit()
            letter_id = letter.id

        resp = await client.post(
            f"{BASE}/dead-letters/{letter_id}/replay",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "replayed": False,
            "status": "REPLAYED",
            "detail": "Already replayed",
        }
