from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.device_directive import DeviceDirective
from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import PromptEventKind, PromptEventSeverity, PromptEventSource
from app.services.device_risk_engine import RiskEngineConfig, evaluate_tenant_risk
from tests.conftest import TEST_TENANT_ID, TestSessionLocal

pytestmark = [pytest.mark.integration]


@pytest.fixture
def admin_auth(tenant_admin_token):
    return {"Authorization": f"Bearer {tenant_admin_token}"}


async def _register_device(client, admin_auth, user_email) -> str:
    r = await client.post(
        "/api/v1/devices/register",
        json={"platform": "macos", "user_external_id": user_email},
        headers=admin_auth,
    )
    return r.json()["device_id"]


@pytest.mark.asyncio
async def test_risk_engine_emits_one_nudge_then_respects_cooldown(client, admin_auth):
    user = "risky@x.io"
    device_id = uuid.UUID(await _register_device(client, admin_auth, user))
    cfg = RiskEngineConfig(min_violations=5, window_hours=24, cooldown_hours=24)

    async with TestSessionLocal() as s:
        s.add(
            PromptEvent(
                id=uuid.uuid4(),
                tenant_id=TEST_TENANT_ID,
                source=PromptEventSource.MACOS_WIDGET,
                event_kind=PromptEventKind.VIOLATION,
                severity=PromptEventSeverity.CRITICAL,
                user_external_id=user,
                occurrences=6,
                occurred_at=datetime.now(UTC),
            )
        )
        await s.commit()

        emitted1 = await evaluate_tenant_risk(s, TEST_TENANT_ID, cfg)
        await s.commit()
        assert emitted1 == 1

        emitted2 = await evaluate_tenant_risk(s, TEST_TENANT_ID, cfg)
        await s.commit()
        assert emitted2 == 0  # cooldown

        directives = (
            (
                await s.execute(
                    select(DeviceDirective).where(
                        DeviceDirective.device_id == device_id,
                        DeviceDirective.origin == "risk_engine",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(directives) == 1
        assert directives[0].kind == "nudge"


@pytest.mark.asyncio
async def test_risk_engine_no_emit_below_threshold(client, admin_auth):
    user = "quiet@x.io"
    await _register_device(client, admin_auth, user)
    cfg = RiskEngineConfig(min_violations=5, window_hours=24)
    async with TestSessionLocal() as s:
        s.add(
            PromptEvent(
                id=uuid.uuid4(),
                tenant_id=TEST_TENANT_ID,
                source=PromptEventSource.MACOS_WIDGET,
                event_kind=PromptEventKind.VIOLATION,
                severity=PromptEventSeverity.HIGH,
                user_external_id=user,
                occurrences=2,
                occurred_at=datetime.now(UTC),
            )
        )
        await s.commit()
        assert await evaluate_tenant_risk(s, TEST_TENANT_ID, cfg) == 0


@pytest.mark.asyncio
async def test_run_for_tenant_emits(client, admin_auth):
    from app.services.device_risk_engine import run_for_tenant

    user = "driver@x.io"
    await _register_device(client, admin_auth, user)
    async with TestSessionLocal() as s:
        s.add(
            PromptEvent(
                id=uuid.uuid4(),
                tenant_id=TEST_TENANT_ID,
                source=PromptEventSource.MACOS_WIDGET,
                event_kind=PromptEventKind.VIOLATION,
                severity=PromptEventSeverity.CRITICAL,
                user_external_id=user,
                occurrences=8,
                occurred_at=datetime.now(UTC),
            )
        )
        await s.commit()
        emitted = await run_for_tenant(s, TEST_TENANT_ID)
        await s.commit()
        assert emitted == 1


@pytest.mark.asyncio
async def test_risk_nudge_body_reflects_the_violation(client, admin_auth):
    """Nudge text names the count, the severity and the app that drove it."""
    user = "detailed@x.io"
    device_id = uuid.UUID(await _register_device(client, admin_auth, user))
    cfg = RiskEngineConfig(min_violations=5, window_hours=24, cooldown_hours=24)

    async with TestSessionLocal() as s:
        s.add_all(
            [
                PromptEvent(
                    id=uuid.uuid4(),
                    tenant_id=TEST_TENANT_ID,
                    source=PromptEventSource.MACOS_WIDGET,
                    event_kind=PromptEventKind.VIOLATION,
                    severity=PromptEventSeverity.CRITICAL,
                    user_external_id=user,
                    app_id="chatgpt",
                    occurrences=6,
                    occurred_at=datetime.now(UTC),
                ),
                PromptEvent(
                    id=uuid.uuid4(),
                    tenant_id=TEST_TENANT_ID,
                    source=PromptEventSource.MACOS_WIDGET,
                    event_kind=PromptEventKind.VIOLATION,
                    severity=PromptEventSeverity.HIGH,
                    user_external_id=user,
                    app_id="claude",
                    occurrences=2,
                    occurred_at=datetime.now(UTC),
                ),
            ]
        )
        await s.commit()

        assert await evaluate_tenant_risk(s, TEST_TENANT_ID, cfg) == 1
        await s.commit()

        directive = (
            (
                await s.execute(
                    select(DeviceDirective).where(
                        DeviceDirective.device_id == device_id,
                        DeviceDirective.origin == "risk_engine",
                    )
                )
            )
            .scalars()
            .one()
        )

        payload = directive.payload
        assert "Critical" in payload["title"]  # a critical event was present
        assert "8" in payload["body"]  # 6 + 2 occurrences
        assert "chatgpt" in payload["body"]  # the heaviest app, not "claude"
        assert "claude" not in payload["body"]
        assert len(payload["title"]) <= 80  # spec §5 caps
        assert len(payload["body"]) <= 280


@pytest.mark.asyncio
async def test_risk_nudge_without_app_id_omits_app_clause(client, admin_auth):
    """app_id is nullable — the body must stay well-formed when it's absent."""
    user = "noapp@x.io"
    device_id = uuid.UUID(await _register_device(client, admin_auth, user))
    cfg = RiskEngineConfig(min_violations=5, window_hours=24, cooldown_hours=24)

    async with TestSessionLocal() as s:
        s.add(
            PromptEvent(
                id=uuid.uuid4(),
                tenant_id=TEST_TENANT_ID,
                source=PromptEventSource.MACOS_WIDGET,
                event_kind=PromptEventKind.VIOLATION,
                severity=PromptEventSeverity.HIGH,
                user_external_id=user,
                app_id=None,
                occurrences=5,
                occurred_at=datetime.now(UTC),
            )
        )
        await s.commit()

        assert await evaluate_tenant_risk(s, TEST_TENANT_ID, cfg) == 1
        await s.commit()

        directive = (
            (
                await s.execute(
                    select(DeviceDirective).where(
                        DeviceDirective.device_id == device_id,
                        DeviceDirective.origin == "risk_engine",
                    )
                )
            )
            .scalars()
            .one()
        )

        body = directive.payload["body"]
        assert "High-risk" in directive.payload["title"]
        assert "5 high-severity policy violations in the last 24h." in body
        assert "mostly in" not in body


@pytest.mark.asyncio
async def test_risk_nudge_is_tagged_policy_violation(client, admin_auth):
    """A violation nudge is tagged as one, and its payload satisfies the same
    NudgeCreateIn contract admin-authored nudges are validated against."""
    from app.schemas.directive import CoachingTag, NudgeCreateIn

    user = "tagged@x.io"
    device_id = uuid.UUID(await _register_device(client, admin_auth, user))
    cfg = RiskEngineConfig(min_violations=5, window_hours=24, cooldown_hours=24)

    async with TestSessionLocal() as s:
        s.add(
            PromptEvent(
                id=uuid.uuid4(),
                tenant_id=TEST_TENANT_ID,
                source=PromptEventSource.MACOS_WIDGET,
                event_kind=PromptEventKind.VIOLATION,
                severity=PromptEventSeverity.HIGH,
                user_external_id=user,
                app_id="chatgpt",
                occurrences=5,
                occurred_at=datetime.now(UTC),
            )
        )
        await s.commit()

        assert await evaluate_tenant_risk(s, TEST_TENANT_ID, cfg) == 1
        await s.commit()

        directive = (
            (
                await s.execute(
                    select(DeviceDirective).where(
                        DeviceDirective.device_id == device_id,
                        DeviceDirective.origin == "risk_engine",
                    )
                )
            )
            .scalars()
            .one()
        )

        assert directive.payload["coaching_tag"] == "policy_violation"
        # extra="forbid" means this also proves the payload carries no stray keys.
        nudge = NudgeCreateIn(**directive.payload)
        assert nudge.coaching_tag is CoachingTag.policy_violation


# ─── Sweep isolation (one tenant's failure must not abort the rest) ──────────


@pytest.mark.asyncio
async def test_sweep_continues_after_a_tenant_fails(client, admin_auth):
    """A tenant whose scoped session can't be opened is recorded and skipped —
    the sweep still evaluates every tenant after it.

    Mirrors the PEP watchdog's convention in routers/pep.py: "one tenant's
    failure never aborts the sweep". The broken tenant is ordered FIRST so an
    abort-on-first-failure implementation emits nothing at all.
    """
    from contextlib import asynccontextmanager

    from app.services.device_risk_engine import sweep_tenants

    user = "sweep@x.io"
    await _register_device(client, admin_auth, user)
    broken_tenant = uuid.uuid4()

    async with TestSessionLocal() as s:
        s.add(
            PromptEvent(
                id=uuid.uuid4(),
                tenant_id=TEST_TENANT_ID,
                source=PromptEventSource.MACOS_WIDGET,
                event_kind=PromptEventKind.VIOLATION,
                severity=PromptEventSeverity.CRITICAL,
                user_external_id=user,
                occurrences=7,
                occurred_at=datetime.now(UTC),
            )
        )
        await s.commit()

    @asynccontextmanager
    async def session_for_tenant(tenant_id):
        if tenant_id == broken_tenant:
            raise RuntimeError("SET LOCAL app.current_tenant_id failed")
        async with TestSessionLocal() as session:
            yield session
            await session.commit()

    seen: list[tuple[uuid.UUID, str]] = []
    result = await sweep_tenants(
        [broken_tenant, TEST_TENANT_ID],
        session_for_tenant,
        on_error=lambda tid, exc: seen.append((tid, str(exc))),
    )

    assert result.emitted == 1  # the healthy tenant still ran
    assert result.tenants_swept == 1
    assert result.failed_tenant_ids == [broken_tenant]
    assert seen == [(broken_tenant, "SET LOCAL app.current_tenant_id failed")]


@pytest.mark.asyncio
async def test_sweep_reports_clean_run_with_no_failures(client, admin_auth):
    """Healthy sweep: every tenant counted, nothing recorded as failed."""
    from contextlib import asynccontextmanager

    from app.services.device_risk_engine import sweep_tenants

    quiet_tenant = uuid.uuid4()

    @asynccontextmanager
    async def session_for_tenant(tenant_id):
        async with TestSessionLocal() as session:
            yield session
            await session.commit()

    result = await sweep_tenants([TEST_TENANT_ID, quiet_tenant], session_for_tenant)

    assert result.tenants_swept == 2
    assert result.emitted == 0  # no violations seeded in this test
    assert result.failed_tenant_ids == []
