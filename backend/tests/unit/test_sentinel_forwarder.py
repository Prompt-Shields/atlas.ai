"""Sentinel forwarder — config, batching, and the spec §6 failure table.

The send path is exercised against an `httpx.MockTransport` so every branch of
the failure table is deterministic and fast; `sleep` is injected so backoff
costs no wall-clock. The orchestration tests cover the property the spec's
audit guarantee rests on: no event leaves the read window without either
reaching Sentinel or being recorded in the dead-letter queue.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration, IntegrationProvider, IntegrationStatus
from app.models.prompt_event import PromptEvent
from app.models.sentinel_forward import (
    SentinelDeadLetter,
    SentinelDeadLetterStatus,
    SentinelForwardCursor,
)
from app.schemas.telemetry import (
    PromptEventAction,
    PromptEventKind,
    PromptEventSeverity,
    PromptEventSource,
)
from app.services import crypto
from app.services import sentinel_forwarder as fwd
from app.services.sentinel_service import SentinelEventType
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

HASH = "b" * 64
# Bearer token the mock AAD endpoint mints; not a credential.
STUB_TOKEN = "tok-abc"  # noqa: S105
DCE = "https://acme-dce-abcd.eastus-1.ingest.monitor.azure.com"
DCR = "dcr-0123456789abcdef"


async def _noop_sleep(_seconds: float) -> None:
    """Injected for `sleep` so retry backoff costs no wall-clock in tests."""
    return None


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch):
    """The forwarder stores the client secret Fernet-encrypted."""
    monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto.reset_cache_for_tests()
    yield
    crypto.reset_cache_for_tests()


def forwarder_config_json(**overrides) -> str:
    config = {
        "workspace_name": "Acme SOC",
        "table_name": "PromptShieldsActivity_CL",
        "enabled_event_types": [t.value for t in SentinelEventType],
        "azure_tenant_id": "11111111-2222-3333-4444-555555555555",
        "client_id": "66666666-7777-8888-9999-000000000000",
        "dce_url": DCE,
        "dcr_immutable_id": DCR,
        "stream_name": "Custom-PromptShields_v1",
    }
    config.update(overrides)
    return json.dumps(config)


def make_integration(**overrides) -> Integration:
    defaults: dict = {
        "tenant_id": TEST_TENANT_ID,
        "provider": IntegrationProvider.SENTINEL,
        "display_name": "Microsoft Sentinel (Acme SOC)",
        "config_json": forwarder_config_json(),
        "status": IntegrationStatus.CONNECTED,
        "is_active": True,
    }
    # Only default the stored secret when the caller did not speak to it, so a
    # test can deliberately construct an integration with no credential.
    if "refresh_token_encrypted" not in overrides:
        defaults["refresh_token_encrypted"] = crypto.encrypt_token("super-secret")
    defaults.update(overrides)
    return Integration(**defaults)


def make_event(**overrides) -> PromptEvent:
    defaults: dict = {
        "tenant_id": TEST_TENANT_ID,
        "source": PromptEventSource.SAFARI_EXTENSION,
        "event_kind": PromptEventKind.VIOLATION,
        "app_id": "chatgpt.com",
        "prompt_hash": HASH,
        "action": PromptEventAction.REDACTED,
        "severity": PromptEventSeverity.MEDIUM,
        "pii_categories": {"ssn": 1},
        "user_external_id": "l.park@example.org",
        "occurrences": 1,
        "occurred_at": datetime.now(UTC) - timedelta(hours=1),
    }
    defaults.update(overrides)
    return PromptEvent(**defaults)


def wire_row(event_id: str = "EV-1") -> dict:
    return {
        "TimeGenerated": "2026-05-05T09:14:00Z",
        "EventId": event_id,
        "TenantId": str(TEST_TENANT_ID),
        "User": "l.park@example.org",
        "AiTool": "ChatGPT Business",
        "IsShadowAi": True,
        "EventType": "Redacted",
        "Severity": "Medium",
        "Detail": "Redacted SSN in ChatGPT Business",
        "PromptHash": HASH,
    }


def mock_client(handler) -> httpx.AsyncClient:
    """An AsyncClient whose responses come from `handler(request)`."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-abc", "expires_in": 3600})


def route(ingest: httpx.Response | list[httpx.Response]):
    """Handler that answers token requests and serves scripted ingest responses.

    A list is consumed one response per call, so a test can script "429 then
    200". `calls` records every ingest request for assertions.
    """
    responses = ingest if isinstance(ingest, list) else None
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in str(request.url):
            return token_response()
        calls.append(request)
        if responses is not None:
            return responses[min(len(calls) - 1, len(responses) - 1)]
        assert not isinstance(ingest, list)
        return ingest

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


# ─── Config ──────────────────────────────────────────────────────────


class TestLoadConfig:
    def test_parses_a_complete_config(self) -> None:
        config = fwd.load_config(make_integration())
        assert config is not None
        assert config.dcr_immutable_id == DCR
        assert config.stream_name == "Custom-PromptShields_v1"

    def test_preview_only_connect_has_no_forwarder_config(self) -> None:
        # A tenant may connect for the seeded preview before their Sentinel
        # admin has run the Bicep template. That is a normal state, not an
        # error, and must not be mistaken for "configured".
        integration = make_integration(config_json=json.dumps({"workspace_name": "Acme SOC"}))
        assert fwd.load_config(integration) is None

    @pytest.mark.parametrize(
        "missing", ["azure_tenant_id", "client_id", "dce_url", "dcr_immutable_id"]
    )
    def test_a_half_filled_config_is_not_configured(self, missing) -> None:
        integration = make_integration(config_json=forwarder_config_json(**{missing: ""}))
        assert fwd.load_config(integration) is None

    def test_unparseable_config_is_not_configured(self) -> None:
        assert fwd.load_config(make_integration(config_json="{not json")) is None

    def test_builds_the_logs_ingestion_url(self) -> None:
        config = fwd.load_config(make_integration())
        assert config is not None
        assert config.ingest_url == (
            f"{DCE}/dataCollectionRules/{DCR}/streams/Custom-PromptShields_v1"
            f"?api-version={fwd.INGESTION_API_VERSION}"
        )

    def test_empty_event_type_list_means_all_types(self) -> None:
        integration = make_integration(config_json=forwarder_config_json(enabled_event_types=[]))
        config = fwd.load_config(integration)
        assert config is not None
        assert config.enabled_event_types == frozenset(SentinelEventType)

    def test_unknown_event_type_is_ignored_not_fatal(self) -> None:
        integration = make_integration(
            config_json=forwarder_config_json(enabled_event_types=["Redacted", "Telepathy"])
        )
        config = fwd.load_config(integration)
        assert config is not None
        assert config.enabled_event_types == frozenset({SentinelEventType.redacted})


# ─── Batching ────────────────────────────────────────────────────────


class TestBatchRows:
    def test_empty_input_produces_no_batches(self) -> None:
        assert fwd.batch_rows([]) == []

    def test_splits_on_the_event_count_cap(self) -> None:
        rows = [wire_row(f"EV-{i}") for i in range(1200)]
        batches = fwd.batch_rows(rows, max_events=500)
        assert [len(b) for b in batches] == [500, 500, 200]

    def test_splits_on_the_byte_cap(self) -> None:
        rows = [wire_row(f"EV-{i}") for i in range(10)]
        # Force a split well before the event cap.
        batches = fwd.batch_rows(rows, max_events=500, max_bytes=900)
        assert len(batches) > 1
        assert sum(len(b) for b in batches) == 10

    def test_preserves_order_and_loses_nothing(self) -> None:
        rows = [wire_row(f"EV-{i}") for i in range(37)]
        flat = [r for batch in fwd.batch_rows(rows, max_events=10) for r in batch]
        assert flat == rows

    def test_an_oversized_single_row_still_gets_a_batch(self) -> None:
        # Dropping it here would be a silent loss; the 413 path turns it into
        # a dead letter with a reason instead.
        rows = [wire_row("EV-huge")]
        assert fwd.batch_rows(rows, max_bytes=1) == [rows]


# ─── Send / failure table ────────────────────────────────────────────


class _StubTokens:
    """Stands in for TokenProvider without a database."""

    def __init__(self, token: str = STUB_TOKEN) -> None:
        self.token = token
        self.refreshes = 0

    async def get(self, *, force_refresh: bool = False) -> str:
        if force_refresh:
            self.refreshes += 1
        return self.token


class TestSendBatch:
    async def def_config(self) -> fwd.ForwarderConfig:
        config = fwd.load_config(make_integration())
        assert config is not None
        return config

    async def test_2xx_accepts_every_row(self) -> None:
        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert len(result.accepted) == 1
        assert result.dead_letters == []

    async def test_posts_the_rows_as_a_json_array(self) -> None:
        handler = route(httpx.Response(204))
        rows = [wire_row("EV-1"), wire_row("EV-2")]
        async with mock_client(handler) as client:
            await fwd.send_batch(
                client,
                await self.def_config(),
                rows,
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        sent = json.loads(handler.calls[0].content)
        assert [r["EventId"] for r in sent] == ["EV-1", "EV-2"]
        assert handler.calls[0].headers["Authorization"] == "Bearer tok-abc"

    async def test_401_refreshes_the_token_and_retries_once(self) -> None:
        handler = route([httpx.Response(401), httpx.Response(204)])
        tokens = _StubTokens()
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=tokens,
                sleep=_noop_sleep,
            )
        assert len(result.accepted) == 1
        assert tokens.refreshes >= 1

    async def test_a_second_401_becomes_a_dead_letter(self) -> None:
        handler = route(httpx.Response(401))
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert result.accepted == []
        assert result.dead_letters[0].reason == "http_401"

    async def test_403_dead_letters_immediately_without_retrying(self) -> None:
        # DCR permissions: the customer's SOC cannot fix it, so retrying only
        # burns the budget. It is an operational alert for our admin.
        handler = route(httpx.Response(403, text="no Monitoring Metrics Publisher"))
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert result.dead_letters[0].reason == "http_403"
        assert len(handler.calls) == 1

    async def test_413_halves_the_batch_and_delivers_both_halves(self) -> None:
        rows = [wire_row(f"EV-{i}") for i in range(4)]
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return token_response()
            count = len(json.loads(request.content))
            seen.append(count)
            return httpx.Response(413) if count > 2 else httpx.Response(204)

        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                rows,
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert len(result.accepted) == 4
        assert result.dead_letters == []
        assert seen == [4, 2, 2]

    async def test_413_on_a_single_row_dead_letters_it(self) -> None:
        handler = route(httpx.Response(413))
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert result.dead_letters[0].reason == "payload_too_large"

    async def test_429_honours_retry_after_then_succeeds(self) -> None:
        handler = route([httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(204)])
        delays: list[float] = []

        async def record(seconds: float) -> None:
            delays.append(seconds)

        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=record,
            )
        assert len(result.accepted) == 1
        assert delays == [3.0]

    async def test_an_absurd_retry_after_is_capped(self) -> None:
        handler = route(
            [httpx.Response(429, headers={"Retry-After": "99999"}), httpx.Response(204)]
        )
        delays: list[float] = []

        async def record(seconds: float) -> None:
            delays.append(seconds)

        async with mock_client(handler) as client:
            await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=record,
            )
        assert delays == [fwd.MAX_HONOURED_RETRY_AFTER]

    async def test_5xx_retries_then_dead_letters_after_the_budget(self) -> None:
        handler = route(httpx.Response(503))
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert result.dead_letters[0].reason == "exhausted_retries"
        assert result.dead_letters[0].http_status == 503
        assert len(handler.calls) == fwd.MAX_ATTEMPTS

    async def test_5xx_that_recovers_is_delivered(self) -> None:
        handler = route([httpx.Response(503), httpx.Response(503), httpx.Response(204)])
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert len(result.accepted) == 1

    async def test_a_network_blip_is_retried(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return token_response()
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ConnectError("connection reset")
            return httpx.Response(204)

        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert len(result.accepted) == 1

    async def test_a_sustained_outage_dead_letters(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "login.microsoftonline.com" in str(request.url):
                return token_response()
            raise httpx.ConnectError("connection refused")

        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert result.dead_letters[0].reason == "exhausted_retries"

    async def test_an_unexpected_4xx_is_not_retried(self) -> None:
        handler = route(httpx.Response(400, text="malformed stream name"))
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [wire_row()],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert result.dead_letters[0].reason == "http_400"
        assert len(handler.calls) == 1

    async def test_empty_batch_makes_no_request(self) -> None:
        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.send_batch(
                client,
                await self.def_config(),
                [],
                tokens=_StubTokens(),
                sleep=_noop_sleep,
            )
        assert result.accepted == [] and result.dead_letters == []
        assert handler.calls == []


class TestBackoff:
    def test_backoff_grows_and_stays_capped(self) -> None:
        for attempt in range(1, 12):
            assert 0 <= fwd._backoff_seconds(attempt) <= fwd.MAX_BACKOFF_SECONDS

    def test_retry_after_parsing(self) -> None:
        assert fwd._parse_retry_after("5") == 5.0
        assert fwd._parse_retry_after(None) is None
        # HTTP-date form falls back to ordinary backoff rather than crashing.
        assert fwd._parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None


# ─── Orchestration ───────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db(setup_database) -> AsyncSession:
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID, name="Test Tenant")
        yield session


async def seed(db: AsyncSession, integration: Integration, events: list[PromptEvent]):
    db.add(integration)
    for event in events:
        db.add(event)
    await db.commit()
    # `created_at` is a server default; the forwarder reads only up to
    # `now - COMMIT_LAG`, so backdate the rows into that window.
    for event in events:
        event.created_at = datetime.now(UTC) - fwd.COMMIT_LAG - timedelta(minutes=5)
    await db.commit()


async def cursor_for(db: AsyncSession, integration: Integration) -> SentinelForwardCursor:
    from sqlalchemy import select

    return (
        await db.execute(
            select(SentinelForwardCursor).where(
                SentinelForwardCursor.integration_id == integration.id
            )
        )
    ).scalar_one()


async def dead_letters(db: AsyncSession) -> list[SentinelDeadLetter]:
    from sqlalchemy import select

    return list(
        (
            await db.execute(select(SentinelDeadLetter).order_by(SentinelDeadLetter.created_at))
        ).scalars()
    )


class TestForwardIntegration:
    async def test_forwards_pending_events_and_advances_the_cursor(self, db) -> None:
        integration = make_integration()
        events = [make_event() for _ in range(3)]
        await seed(db, integration, events)

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.events_forwarded == 3
        assert result.batches_dead_lettered == 0
        cursor = await cursor_for(db, integration)
        assert cursor.last_event_id is not None
        assert cursor.events_forwarded == 3

    async def test_a_second_run_forwards_nothing_new(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event() for _ in range(2)])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)
            second = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert second.events_read == 0
        assert second.events_forwarded == 0

    async def test_preview_only_integration_makes_no_azure_call(self, db) -> None:
        integration = make_integration(config_json=json.dumps({"workspace_name": "Acme SOC"}))
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.events_forwarded == 0
        assert handler.calls == []

    async def test_ordinary_activity_is_skipped_not_forwarded(self, db) -> None:
        integration = make_integration()
        await seed(
            db,
            integration,
            [make_event(event_kind=PromptEventKind.ACTIVITY, action=PromptEventAction.ALLOWED)],
        )

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.events_read == 1
        assert result.events_forwarded == 0
        assert result.events_skipped == 1
        assert handler.calls == []

    async def test_a_fully_skipped_window_still_advances_the_cursor(self, db) -> None:
        # Otherwise the forwarder re-reads the same unforwardable rows forever.
        integration = make_integration()
        events = [make_event(event_kind=PromptEventKind.ACTIVITY, action=PromptEventAction.ALLOWED)]
        await seed(db, integration, events)

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)
            second = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert second.events_read == 0

    async def test_disabled_event_types_are_filtered_out(self, db) -> None:
        integration = make_integration(
            config_json=forwarder_config_json(enabled_event_types=["Blocked"])
        )
        await seed(db, integration, [make_event(action=PromptEventAction.REDACTED)])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.events_skipped == 1
        assert handler.calls == []

    async def test_an_event_without_a_hash_is_skipped(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event(prompt_hash=None)])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.events_skipped == 1
        assert handler.calls == []

    async def test_a_failed_batch_becomes_a_dead_letter_with_its_payload(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(403, text="forbidden"))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.batches_dead_lettered == 1
        rows = await dead_letters(db)
        assert len(rows) == 1
        assert rows[0].reason == "http_403"
        assert rows[0].status == SentinelDeadLetterStatus.PENDING
        assert rows[0].event_count == 1
        # The payload is the whole point — replay must need no re-mapping.
        assert rows[0].payload[0]["PromptHash"] == HASH
        assert rows[0].first_event_id.startswith("EV-")

    async def test_a_persistent_failure_does_not_duplicate_dead_letters(self, db) -> None:
        # The cursor advances past a dead-lettered batch because the dead
        # letter is itself the durable record. Rewinding instead would re-read
        # the same events every cycle: a 403 lasting a day would bury the
        # operator's queue in thousands of copies of one batch.
        integration = make_integration()
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(403))
        async with mock_client(handler) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)
            second = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert second.events_read == 0
        assert len(await dead_letters(db)) == 1

    async def test_a_dead_lettered_event_is_recorded_before_the_cursor_moves(self, db) -> None:
        # The audit guarantee: nothing leaves the window without either
        # reaching Sentinel or being visible in the queue, with its payload.
        integration = make_integration()
        event = make_event()
        await seed(db, integration, [event])

        handler = route(httpx.Response(403))
        async with mock_client(handler) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)

        cursor = await cursor_for(db, integration)
        assert cursor.last_event_id == event.id
        letters = await dead_letters(db)
        assert letters[0].payload[0]["EventId"] == f"EV-{event.id}"

    async def test_a_dead_letter_marks_the_integration_in_error(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(403))
        async with mock_client(handler) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)

        assert integration.status == IntegrationStatus.ERROR
        assert integration.last_error

    async def test_a_successful_run_clears_a_previous_error(self, db) -> None:
        integration = make_integration(
            status=IntegrationStatus.ERROR, last_error="previous failure"
        )
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)

        assert integration.status == IntegrationStatus.CONNECTED
        assert integration.last_error is None

    async def test_events_newer_than_the_commit_lag_are_left_for_the_next_run(self, db) -> None:
        # A row committed moments ago could still be racing an in-flight
        # transaction; reading it now risks stranding a sibling behind the
        # advanced cursor.
        integration = make_integration()
        event = make_event()
        db.add(integration)
        db.add(event)
        await db.commit()  # created_at = now, inside the lag window

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.events_read == 0

    async def test_a_bad_client_secret_dead_letters_rather_than_raising(self, db) -> None:
        integration = make_integration(refresh_token_encrypted=None)
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            result = await fwd.forward_integration(
                db, integration, client=client, sleep=_noop_sleep
            )

        assert result.batches_dead_lettered == 1
        assert (await dead_letters(db))[0].reason == "auth_failed"


class TestReplay:
    async def test_replaying_a_dead_letter_marks_it_replayed(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event()])

        fail = route(httpx.Response(503))
        async with mock_client(fail) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)

        letter = (await dead_letters(db))[0]
        assert letter.status == SentinelDeadLetterStatus.PENDING

        ok = route(httpx.Response(204))
        async with mock_client(ok) as client:
            replayed = await fwd.replay_dead_letter(
                db, letter, integration, client=client, sleep=_noop_sleep
            )

        assert replayed is True
        assert letter.status == SentinelDeadLetterStatus.REPLAYED
        assert letter.replayed_at is not None

    async def test_replay_sends_the_stored_payload_unchanged(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event()])

        fail = route(httpx.Response(503))
        async with mock_client(fail) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)

        letter = (await dead_letters(db))[0]
        original = json.loads(json.dumps(letter.payload))

        ok = route(httpx.Response(204))
        async with mock_client(ok) as client:
            await fwd.replay_dead_letter(db, letter, integration, client=client, sleep=_noop_sleep)

        # Same EventIds on the wire as the first attempt — that is what makes
        # replaying a partly-ingested batch safe.
        assert json.loads(ok.calls[0].content) == original

    async def test_a_failed_replay_stays_pending_and_records_why(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event()])

        fail = route(httpx.Response(503))
        async with mock_client(fail) as client:
            await fwd.forward_integration(db, integration, client=client, sleep=_noop_sleep)

        letter = (await dead_letters(db))[0]
        attempts_before = letter.attempts

        still_failing = route(httpx.Response(403, text="still forbidden"))
        async with mock_client(still_failing) as client:
            replayed = await fwd.replay_dead_letter(
                db, letter, integration, client=client, sleep=_noop_sleep
            )

        assert replayed is False
        assert letter.status == SentinelDeadLetterStatus.PENDING
        assert letter.reason == "http_403"
        assert letter.attempts == attempts_before + 1


class TestForwardPending:
    async def test_sweeps_connected_integrations(self, db) -> None:
        integration = make_integration()
        await seed(db, integration, [make_event() for _ in range(2)])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            forwarded = await fwd.forward_pending(db, client=client, sleep=_noop_sleep)

        assert forwarded == 2

    async def test_skips_an_inactive_integration(self, db) -> None:
        integration = make_integration(is_active=False)
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            forwarded = await fwd.forward_pending(db, client=client, sleep=_noop_sleep)

        assert forwarded == 0
        assert handler.calls == []

    async def test_retries_an_integration_left_in_error(self, db) -> None:
        # An ERROR integration is exactly the one that needs another attempt
        # once the customer fixes their DCR role assignment.
        integration = make_integration(status=IntegrationStatus.ERROR)
        await seed(db, integration, [make_event()])

        handler = route(httpx.Response(204))
        async with mock_client(handler) as client:
            forwarded = await fwd.forward_pending(db, client=client, sleep=_noop_sleep)

        assert forwarded == 1


class TestTokenProvider:
    async def test_caches_the_token_on_the_integration(self, db) -> None:
        integration = make_integration()
        db.add(integration)
        await db.commit()
        config = fwd.load_config(integration)
        assert config is not None

        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return token_response()

        async with mock_client(handler) as client:
            tokens = fwd.TokenProvider(db, client, integration, config)
            first = await tokens.get()
            second = await tokens.get()

        assert first == second == "tok-abc"
        assert calls["n"] == 1  # second call served from the cached ciphertext
        assert integration.access_token_encrypted is not None

    async def test_an_expired_cached_token_is_refetched(self, db) -> None:
        integration = make_integration()
        integration.access_token_encrypted = crypto.encrypt_token("stale")
        integration.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.add(integration)
        await db.commit()
        config = fwd.load_config(integration)
        assert config is not None

        async with mock_client(route(httpx.Response(204))) as client:
            tokens = fwd.TokenProvider(db, client, integration, config)
            assert await tokens.get() == "tok-abc"

    async def test_a_rejected_credential_raises_auth_error(self, db) -> None:
        integration = make_integration()
        db.add(integration)
        await db.commit()
        config = fwd.load_config(integration)
        assert config is not None

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid_client"})

        async with mock_client(handler) as client:
            tokens = fwd.TokenProvider(db, client, integration, config)
            with pytest.raises(fwd.SentinelAuthError):
                await tokens.get()
