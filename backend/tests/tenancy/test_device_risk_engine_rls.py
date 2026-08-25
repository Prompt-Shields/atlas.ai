"""Postgres RLS verification for the Phase-4 device risk engine.

PR #276 shipped `run_device_risk_engine` relying on `tenant_scoped_session`'s
`SET LOCAL app.current_tenant_id` for per-tenant isolation, but left that
reliance unverified: the default unit-test DB is sqlite, which has no RLS and
treats `set_tenant_guc` as a no-op. These tests close that gap by driving the
real engine against real policies.

Postgres-only; skipped on sqlite. CI runs the suite against pgvector/pgvector:pg16,
so these execute there. Locally, point TEST_DATABASE_URL at a Postgres instance.

Harness notes (same rationale as `test_rls_enforced.py`, see its docstrings):

- conftest's `setup_database` builds the tables from the ORM via `create_all`,
  which does NOT run the Alembic migrations — so the policies are installed
  here, byte-identical to the `USING` clauses in the migrations that create
  the tables: `*_developer_api_enablement` (`grc.prompt_events`),
  `*_enrolled_device` and `*_device_directive`. Referenced by name rather than
  revision id — the ids get renumbered when collisions are untangled.
- FORCE ROW LEVEL SECURITY is required because the test role owns the tables
  and owners bypass plain ENABLE. Superusers bypass RLS even under FORCE, so
  the assertions drop to a NOLOGIN NOSUPERUSER role via `SET LOCAL ROLE`.
- `FOR ALL` with only a `USING` clause doubles as `WITH CHECK`, so the engine's
  directive INSERT is itself an isolation assertion: it only succeeds because
  the row's tenant_id matches the GUC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.models.device_directive import DeviceDirective
from app.models.enrolled_device import EnrolledDevice
from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import PromptEventKind, PromptEventSeverity, PromptEventSource
from app.services.device_risk_engine import RiskEngineConfig, evaluate_tenant_risk

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

TENANT_A = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
TENANT_B = uuid.UUID("00000000-0000-0000-0000-0000000000b3")

# (table, policy name) — policy names match the migrations that create them.
_RLS_TABLES = (
    ("grc.prompt_events", "tenant_isolation_prompt_events_test"),
    ("grc.enrolled_devices", "tenant_isolation_enrolled_devices_test"),
    ("grc.device_directives", "tenant_isolation_device_directives_test"),
)


async def _install_rls(session) -> None:
    """Enable + force RLS and install the migrations' tenant policy on each
    table the risk engine touches, then grant a non-superuser probe role the
    minimum rights the engine needs (read telemetry + devices, write nudges)."""
    for table, policy in _RLS_TABLES:
        await session.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        await session.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        await session.execute(text(f"DROP POLICY IF EXISTS {policy} ON {table}"))
        await session.execute(
            text(
                f"""
                CREATE POLICY {policy} ON {table}
                USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
                """
            )
        )

    await session.execute(
        text(
            """
            DO $$ BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'rls_check_role'
              ) THEN
                CREATE ROLE rls_check_role NOLOGIN NOSUPERUSER;
              END IF;
            END $$
            """
        )
    )
    await session.execute(text("GRANT USAGE ON SCHEMA grc TO rls_check_role"))
    await session.execute(
        text("GRANT SELECT ON grc.prompt_events, grc.enrolled_devices TO rls_check_role")
    )
    await session.execute(text("GRANT SELECT, INSERT ON grc.device_directives TO rls_check_role"))
    await session.execute(text("GRANT rls_check_role TO CURRENT_USER"))


def _violation(tenant_id: uuid.UUID, user: str) -> PromptEvent:
    return PromptEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source=PromptEventSource.MACOS_WIDGET,
        event_kind=PromptEventKind.VIOLATION,
        severity=PromptEventSeverity.CRITICAL,
        user_external_id=user,
        app_id="chatgpt",
        occurrences=9,
        occurred_at=datetime.now(UTC),
    )


def _device(tenant_id: uuid.UUID, user: str) -> EnrolledDevice:
    return EnrolledDevice(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_external_id=user,
        platform="macos",
        enrollment_state="active",
        token_hash=uuid.uuid4().hex * 2,  # 64 chars, matches the sha256 column
    )


async def _seed_both_tenants(session) -> tuple[uuid.UUID, uuid.UUID]:
    """One at-risk user with one active device per tenant. Returns (device_a,
    device_b) ids. Seeded as the table owner, which bypasses RLS."""
    device_a, device_b = _device(TENANT_A, "shared@x.io"), _device(TENANT_B, "shared@x.io")
    session.add_all(
        [
            device_a,
            device_b,
            _violation(TENANT_A, "shared@x.io"),
            _violation(TENANT_B, "shared@x.io"),
        ]
    )
    await session.commit()
    return device_a.id, device_b.id


async def _skip_unless_postgres(session) -> None:
    if session.get_bind().dialect.name != "postgresql":
        pytest.skip("RLS enforcement requires Postgres")


async def test_engine_under_tenant_guc_emits_only_for_its_own_tenant() -> None:
    """The full engine run, executed exactly the way the worker runs it: GUC
    set to one tenant, non-superuser role, real policies in force.

    Both tenants have an identically-named at-risk user over threshold. A
    tenant-A run must emit exactly one nudge, to tenant A's device.

    This asserts the engine *works* under RLS — in particular that its
    directive INSERT satisfies the policy's implicit WITH CHECK and that a
    least-privilege role suffices. It is not on its own a proof of isolation
    (the engine also filters by tenant_id in SQL); the two tests below cover
    that, and both fail if the policies are missing.
    """
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as setup:
        await _skip_unless_postgres(setup)
        await _install_rls(setup)
        await setup.commit()

    async with TestSessionLocal() as setup:
        device_a, device_b = await _seed_both_tenants(setup)

    async with TestSessionLocal() as session:
        # Exactly what worker_app.tenant_session.tenant_scoped_session does.
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{TENANT_A}'"))
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        emitted = await evaluate_tenant_risk(session, TENANT_A, RiskEngineConfig(min_violations=5))
        # Flush inside the GUC'd transaction: the INSERT must satisfy the
        # policy's implicit WITH CHECK, so this is an assertion in itself.
        await session.flush()
        assert emitted == 1
        await session.commit()

    async with TestSessionLocal() as check:
        rows = (await check.execute(select(DeviceDirective.device_id))).scalars().all()
        assert rows == [device_a]
        assert device_b not in rows


async def test_cross_tenant_reads_are_blocked_even_when_explicitly_queried() -> None:
    """RLS is the backstop, not the tenant_id filter in the engine's SQL.

    Under tenant A's GUC, a query that explicitly asks for tenant B's
    telemetry and devices still returns nothing.
    """
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as setup:
        await _skip_unless_postgres(setup)
        await _install_rls(setup)
        await setup.commit()

    async with TestSessionLocal() as setup:
        await _seed_both_tenants(setup)

    async with TestSessionLocal() as session:
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{TENANT_A}'"))
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        leaked_events = (
            (await session.execute(select(PromptEvent.id).where(PromptEvent.tenant_id == TENANT_B)))
            .scalars()
            .all()
        )
        leaked_devices = (
            (
                await session.execute(
                    select(EnrolledDevice.id).where(EnrolledDevice.tenant_id == TENANT_B)
                )
            )
            .scalars()
            .all()
        )

        assert leaked_events == []
        assert leaked_devices == []

        # ...and tenant A's own rows are still visible, so the empty results
        # above are isolation rather than an empty table.
        own_events = (
            (await session.execute(select(PromptEvent.id).where(PromptEvent.tenant_id == TENANT_A)))
            .scalars()
            .all()
        )
        assert len(own_events) == 1


async def test_unset_guc_fails_closed_for_the_engine() -> None:
    """A worker that never set the GUC must emit nothing, not everything.

    `set_config` cannot truly unset a GUC mid-transaction, so the nil UUID
    stands in for "unset" — it matches no real tenant_id, so every policy
    evaluates false.
    """
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as setup:
        await _skip_unless_postgres(setup)
        await _install_rls(setup)
        await setup.commit()

    async with TestSessionLocal() as setup:
        await _seed_both_tenants(setup)

    async with TestSessionLocal() as session:
        await session.execute(
            text(
                "SELECT set_config('app.current_tenant_id',"
                " '00000000-0000-0000-0000-000000000000', true)"
            )
        )
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        emitted = await evaluate_tenant_risk(session, TENANT_A, RiskEngineConfig(min_violations=5))
        await session.flush()
        assert emitted == 0
        await session.commit()

    async with TestSessionLocal() as check:
        assert (await check.execute(select(DeviceDirective.id))).scalars().all() == []
