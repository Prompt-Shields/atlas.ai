"""
Regression test: Postgres RLS tenant GUC is actually enforced.

This test is intentionally Postgres-only. The default unit-test DB in this repo
is sqlite, which cannot validate Postgres RLS behavior.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.database import set_tenant_guc

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_postgres_rls_enforced_via_app_current_tenant_id() -> None:
    """RLS on a throwaway probe table isolates rows by tenant GUC.

    The connecting test role owns grc.rls_probe, and Postgres table owners
    bypass RLS unless FORCE ROW LEVEL SECURITY is set.  Superusers bypass RLS
    even with FORCE, so the SELECT assertions drop to a minimal NOLOGIN
    NOSUPERUSER helper role (rls_check_role) via SET LOCAL ROLE.  This mirrors
    test_postgres_rls_enforced_on_prompt_events below; see its docstring for
    the full rationale, including why INSERTs set the GUC to each row's own
    tenant_id (FOR ALL with only USING doubles as WITH CHECK under FORCE RLS)
    and why the nil UUID stands in for "unset" in the fail-closed assertion.
    """
    from tests.conftest import TestSessionLocal

    tenant_a = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
    tenant_b = uuid.UUID("00000000-0000-0000-0000-0000000000b1")

    async with TestSessionLocal() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("RLS enforcement requires Postgres")

        # Create a tiny probe table protected by the same pattern used in migrations.
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS grc"))
        await session.execute(text("DROP TABLE IF EXISTS grc.rls_probe"))
        await session.execute(
            text(
                """
                CREATE TABLE grc.rls_probe (
                  id uuid PRIMARY KEY,
                  tenant_id uuid NOT NULL,
                  payload text NOT NULL
                )
                """
            )
        )
        # FORCE is required: the test role owns the table, and owners bypass
        # RLS under plain ENABLE.
        await session.execute(text("ALTER TABLE grc.rls_probe ENABLE ROW LEVEL SECURITY"))
        await session.execute(text("ALTER TABLE grc.rls_probe FORCE ROW LEVEL SECURITY"))
        await session.execute(
            text("DROP POLICY IF EXISTS tenant_isolation_rls_probe ON grc.rls_probe")
        )
        await session.execute(
            text(
                """
                CREATE POLICY tenant_isolation_rls_probe ON grc.rls_probe
                USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
                """
            )
        )

        # Non-superuser role for the SELECT-phase assertions: superusers
        # bypass RLS even with FORCE ROW LEVEL SECURITY.
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
        await session.execute(text("GRANT SELECT ON grc.rls_probe TO rls_check_role"))
        # SET ROLE requires membership (superusers excepted).  The creating
        # role holds ADMIN on rls_check_role, so it can grant itself
        # membership; this makes the test work for non-superuser CREATEROLE
        # connections too.
        await session.execute(text("GRANT rls_check_role TO CURRENT_USER"))

        # Insert rows for two tenants, setting the GUC to each row's own
        # tenant_id first: FOR ALL with only a USING clause doubles as WITH
        # CHECK, so under FORCE RLS a mismatched GUC rejects the INSERT.
        await set_tenant_guc(session, tenant_a)
        await session.execute(
            text(
                """
                INSERT INTO grc.rls_probe (id, tenant_id, payload)
                VALUES (:id, :tenant_id, :payload)
                """
            ),
            {"id": str(uuid.uuid4()), "tenant_id": str(tenant_a), "payload": "A"},
        )
        await set_tenant_guc(session, tenant_b)
        await session.execute(
            text(
                """
                INSERT INTO grc.rls_probe (id, tenant_id, payload)
                VALUES (:id, :tenant_id, :payload)
                """
            ),
            {"id": str(uuid.uuid4()), "tenant_id": str(tenant_b), "payload": "B"},
        )

        # set_config cannot truly unset a GUC mid-transaction; the nil UUID is
        # a sentinel that matches no real tenant_id, so the policy evaluates
        # false for every row.
        await session.execute(
            text(
                "SELECT set_config('app.current_tenant_id',"
                " '00000000-0000-0000-0000-000000000000', true)"
            )
        )

        # Drop to the non-superuser role for the visibility checks.
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        # Fail closed when the GUC matches no tenant.
        res_unset = await session.execute(text("SELECT count(*) FROM grc.rls_probe"))
        assert (res_unset.scalar() or 0) == 0

        # Tenant A sees only its row.  SET statements do not accept bind
        # parameters; the value is a validated uuid.UUID so interpolation is safe.
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_a}'"))
        res_a = await session.execute(
            text("SELECT array_agg(payload ORDER BY payload) FROM grc.rls_probe")
        )
        assert (res_a.scalar() or []) == ["A"]

        # Tenant B sees only its row
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_b}'"))
        res_b = await session.execute(
            text("SELECT array_agg(payload ORDER BY payload) FROM grc.rls_probe")
        )
        assert (res_b.scalar() or []) == ["B"]


async def test_postgres_rls_enforced_on_prompt_events() -> None:
    """RLS policy on the real grc.prompt_events table isolates rows by tenant.

    When conftest's setup_database fixture runs Base.metadata.create_all, it
    builds the real grc.prompt_events table from the ORM model definition.
    The CREATE TABLE IF NOT EXISTS DDL below therefore no-ops in that case; it
    only takes effect on a standalone invocation where create_all did not run.

    Three important notes about testing RLS on this table:

    1. pii_categories and occurrences have Python-side defaults only in the ORM
       model; create_all does NOT emit server defaults for them.  Raw INSERTs
       must supply both columns explicitly.

    2. The test role owns the table and Postgres owners bypass RLS by default.
       FORCE ROW LEVEL SECURITY is therefore required so the policy fires for
       the owning role.  However, Postgres superusers bypass RLS even with
       FORCE ROW LEVEL SECURITY.  To handle both superuser and non-superuser
       test environments, we create a minimal non-superuser helper role
       (rls_check_role) and use SET LOCAL ROLE to drop privileges for the
       SELECT assertions.  INSERTs run with the GUC set to each row's own
       tenant_id so they satisfy the policy's implicit WITH CHECK even under
       FORCE RLS with a non-superuser owner; see note 4 below.

    3. The policy installed here (tenant_isolation_test) uses a USING clause
       that is byte-identical to migration 019's tenant_isolation policy.

    4. Because FOR ALL with only a USING clause doubles as WITH CHECK, a
       non-superuser table owner running under FORCE ROW LEVEL SECURITY will
       receive "new row violates row-level security policy" on any INSERT where
       the GUC does not match the row's tenant_id.  Inserts therefore set the
       GUC to the row's own tenant_id first so they satisfy the implicit WITH
       CHECK even under FORCE RLS with a non-superuser owner.  After the
       inserts the GUC is set to the nil UUID (a sentinel that matches no
       real tenant_id) to verify fail-closed behaviour, then SELECT
       assertions run under the NOLOGIN rls_check_role.  Note: set_config
       cannot truly unset a GUC mid-transaction; the nil UUID is the
       closest equivalent to "unset" within one transaction.
    """
    from tests.conftest import TestSessionLocal

    tenant_a = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
    tenant_b = uuid.UUID("00000000-0000-0000-0000-0000000000b2")

    async with TestSessionLocal() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("RLS enforcement requires Postgres")

        # Ensure the schema exists (conftest create_all may have already done
        # this, but be defensive).
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS grc"))

        # Ensure the grc.prompt_events table exists.  conftest's setup_database
        # fixture calls Base.metadata.create_all which covers the ORM model, so
        # this DDL no-ops in the normal case.  It only takes effect on a
        # standalone invocation where create_all did not run.  Note: source and
        # event_kind are declared as text here to avoid enum pre-requisites in
        # the standalone path; create_all uses the real enum types instead.
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS grc.prompt_events (
                    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id      uuid NOT NULL,
                    source         text NOT NULL,
                    event_kind     text NOT NULL,
                    pii_categories jsonb NOT NULL DEFAULT '{}'::jsonb,
                    occurrences    integer NOT NULL DEFAULT 1,
                    occurred_at    timestamptz NOT NULL DEFAULT now(),
                    created_at     timestamptz NOT NULL DEFAULT now(),
                    updated_at     timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )

        # Enable RLS, and force it even for the table owner (otherwise the
        # owning role bypasses RLS entirely).
        await session.execute(text("ALTER TABLE grc.prompt_events ENABLE ROW LEVEL SECURITY"))
        await session.execute(text("ALTER TABLE grc.prompt_events FORCE ROW LEVEL SECURITY"))
        await session.execute(
            text("DROP POLICY IF EXISTS tenant_isolation_test ON grc.prompt_events")
        )
        await session.execute(
            text(
                """
                CREATE POLICY tenant_isolation_test ON grc.prompt_events
                USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)
                """
            )
        )

        # Create a minimal non-superuser role for the SELECT-phase assertions.
        # Superusers bypass RLS even with FORCE ROW LEVEL SECURITY, so we drop
        # to this role before running the visibility checks.  The role is
        # idempotent: DO ... IF NOT EXISTS guards the CREATE.
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
        await session.execute(text("GRANT SELECT ON grc.prompt_events TO rls_check_role"))
        # SET ROLE requires membership (superusers excepted).  The creating
        # role holds ADMIN on rls_check_role, so it can grant itself
        # membership; this makes the test work for non-superuser CREATEROLE
        # connections too.
        await session.execute(text("GRANT rls_check_role TO CURRENT_USER"))

        # Insert one row per tenant.  pii_categories and occurrences must be
        # supplied explicitly: the ORM model defines only Python-side defaults
        # for both, so create_all does not emit server defaults and raw INSERTs
        # would otherwise hit a NOT NULL violation.
        #
        # The GUC must be set to each row's own tenant_id before its INSERT.
        # FOR ALL with only a USING clause doubles as WITH CHECK, so under
        # FORCE RLS a non-superuser table owner gets "new row violates
        # row-level security policy" when the GUC does not match tenant_id.
        # Setting the GUC first both satisfies the implicit WITH CHECK and
        # dogfoods the set_tenant_guc helper.  SELECT assertions then run
        # under the NOLOGIN rls_check_role (see below).
        row_a_id = str(uuid.uuid4())
        row_b_id = str(uuid.uuid4())
        await set_tenant_guc(session, tenant_a)
        await session.execute(
            text(
                """
                INSERT INTO grc.prompt_events
                    (id, tenant_id, source, event_kind, pii_categories, occurrences)
                VALUES (:id, :tenant_id, 'safari_extension', 'activity', '{}'::jsonb, 1)
                """
            ),
            {"id": row_a_id, "tenant_id": str(tenant_a)},
        )
        await set_tenant_guc(session, tenant_b)
        await session.execute(
            text(
                """
                INSERT INTO grc.prompt_events
                    (id, tenant_id, source, event_kind, pii_categories, occurrences)
                VALUES (:id, :tenant_id, 'sdk', 'violation', '{}'::jsonb, 1)
                """
            ),
            {"id": row_b_id, "tenant_id": str(tenant_b)},
        )

        # Point the GUC at the nil UUID (a sentinel that matches no tenant_id
        # stored in the table) so the "fail closed" assertion below sees zero
        # rows.  After the last INSERT the GUC still holds tenant_b's value;
        # we cannot truly unset a GUC mid-transaction via set_config, so the
        # nil UUID is the closest approximation: the policy evaluates to
        # 'some-uuid = nil-uuid' which is false for every row.
        await session.execute(
            text(
                "SELECT set_config('app.current_tenant_id',"
                " '00000000-0000-0000-0000-000000000000', true)"
            )
        )

        # Drop to the non-superuser role for visibility checks.  SET LOCAL
        # scopes the role to the current transaction so it rolls back cleanly.
        # Superusers bypass FORCE ROW LEVEL SECURITY, so this step is required
        # for the RLS assertions to fire correctly on superuser connections.
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        # Fail closed when GUC holds a nil/unmatched UUID (policy evaluates
        # tenant_id = nil-uuid, which is false for every row -> 0 rows).
        res_unset = await session.execute(
            text("SELECT count(*) FROM grc.prompt_events WHERE id IN (:a, :b)"),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_unset.scalar() or 0) == 0

        # Tenant A sees only its row.
        # Note: Postgres SET statements do not accept positional parameters
        # ($1), so we format the UUID value directly into the SQL string.
        # UUIDs are safe to interpolate: they are validated by uuid.UUID().
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_a}'"))
        res_a = await session.execute(
            text(
                "SELECT array_agg(source ORDER BY source) "
                "FROM grc.prompt_events WHERE id IN (:a, :b)"
            ),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_a.scalar() or []) == ["safari_extension"]

        # Tenant B sees only its row.
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_b}'"))
        res_b = await session.execute(
            text(
                "SELECT array_agg(source ORDER BY source) "
                "FROM grc.prompt_events WHERE id IN (:a, :b)"
            ),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_b.scalar() or []) == ["sdk"]


async def test_postgres_rls_enforced_on_sentinel_dead_letters() -> None:
    """RLS on grc.sentinel_dead_letters isolates rows by tenant.

    Dead letters hold the full outbound payload of an undelivered batch —
    every mapped column for every event in it. A cross-tenant read here would
    leak one customer's AI activity to another, so the table gets the same
    scrutiny as prompt_events itself.

    Mechanics follow test_postgres_rls_enforced_on_prompt_events exactly: FORCE
    ROW LEVEL SECURITY because the test role owns the table, the NOLOGIN
    rls_check_role for the SELECT assertions because superusers bypass FORCE,
    the GUC set to each row's own tenant_id before its INSERT (FOR ALL with
    only USING doubles as WITH CHECK), and the nil UUID standing in for
    "unset". See that test's docstring for the full rationale.

    As with prompt_events, `event_count`/`attempts`/`status` carry Python-side
    ORM defaults only, so `create_all` emits no server defaults for them and
    these raw INSERTs must supply them explicitly. Migration 042 does declare
    server defaults, so this is a create_all artefact, not a schema gap.
    """
    from tests.conftest import TestSessionLocal

    tenant_a = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
    tenant_b = uuid.UUID("00000000-0000-0000-0000-0000000000b3")

    async with TestSessionLocal() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("RLS enforcement requires Postgres")

        await session.execute(text("CREATE SCHEMA IF NOT EXISTS grc"))

        # conftest's create_all builds this from the ORM model, so the DDL
        # no-ops in the normal case. `status` and `integration_id` are declared
        # as text/uuid here to avoid enum and FK prerequisites on the
        # standalone path.
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS grc.sentinel_dead_letters (
                    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id      uuid NOT NULL,
                    integration_id uuid NOT NULL,
                    status         text NOT NULL DEFAULT 'PENDING',
                    reason         varchar(100) NOT NULL,
                    payload        jsonb NOT NULL DEFAULT '[]'::jsonb,
                    event_count    integer NOT NULL DEFAULT 0,
                    attempts       integer NOT NULL DEFAULT 1,
                    created_at     timestamptz NOT NULL DEFAULT now(),
                    updated_at     timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )

        await session.execute(
            text("ALTER TABLE grc.sentinel_dead_letters ENABLE ROW LEVEL SECURITY")
        )
        await session.execute(
            text("ALTER TABLE grc.sentinel_dead_letters FORCE ROW LEVEL SECURITY")
        )
        await session.execute(
            text("DROP POLICY IF EXISTS tenant_isolation_test ON grc.sentinel_dead_letters")
        )
        # Byte-identical to migration 042's tenant_isolation policy.
        await session.execute(
            text(
                """
                CREATE POLICY tenant_isolation_test ON grc.sentinel_dead_letters
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
        await session.execute(text("GRANT SELECT ON grc.sentinel_dead_letters TO rls_check_role"))
        await session.execute(text("GRANT rls_check_role TO CURRENT_USER"))

        # The dead-letter rows carry a real FK to grc.integrations, so each
        # tenant needs an integration to hang them off.
        integration_a = str(uuid.uuid4())
        integration_b = str(uuid.uuid4())

        async def _seed_integration(integration_id: str, tenant_id: uuid.UUID) -> None:
            await set_tenant_guc(session, tenant_id)
            await session.execute(
                text(
                    """
                    INSERT INTO grc.integrations
                        (id, tenant_id, provider, status, display_name,
                         is_active, is_test_data)
                    VALUES (:id, :tenant_id, 'SENTINEL', 'CONNECTED',
                            'Microsoft Sentinel', true, true)
                    """
                ),
                {"id": integration_id, "tenant_id": str(tenant_id)},
            )

        await _seed_integration(integration_a, tenant_a)
        await _seed_integration(integration_b, tenant_b)

        row_a_id = str(uuid.uuid4())
        row_b_id = str(uuid.uuid4())
        await set_tenant_guc(session, tenant_a)
        await session.execute(
            text(
                """
                INSERT INTO grc.sentinel_dead_letters
                    (id, tenant_id, integration_id, status, reason, payload,
                     event_count, attempts)
                VALUES (:id, :tenant_id, :integration_id, 'PENDING', 'http_403',
                        '[]'::jsonb, 1, 1)
                """
            ),
            {
                "id": row_a_id,
                "tenant_id": str(tenant_a),
                "integration_id": integration_a,
            },
        )
        await set_tenant_guc(session, tenant_b)
        await session.execute(
            text(
                """
                INSERT INTO grc.sentinel_dead_letters
                    (id, tenant_id, integration_id, status, reason, payload,
                     event_count, attempts)
                VALUES (:id, :tenant_id, :integration_id, 'PENDING', 'exhausted_retries',
                        '[]'::jsonb, 1, 1)
                """
            ),
            {
                "id": row_b_id,
                "tenant_id": str(tenant_b),
                "integration_id": integration_b,
            },
        )

        await session.execute(
            text(
                "SELECT set_config('app.current_tenant_id',"
                " '00000000-0000-0000-0000-000000000000', true)"
            )
        )
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        # Fail closed on an unmatched GUC.
        res_unset = await session.execute(
            text("SELECT count(*) FROM grc.sentinel_dead_letters WHERE id IN (:a, :b)"),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_unset.scalar() or 0) == 0

        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_a}'"))
        res_a = await session.execute(
            text(
                "SELECT array_agg(reason ORDER BY reason) "
                "FROM grc.sentinel_dead_letters WHERE id IN (:a, :b)"
            ),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_a.scalar() or []) == ["http_403"]

        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_b}'"))
        res_b = await session.execute(
            text(
                "SELECT array_agg(reason ORDER BY reason) "
                "FROM grc.sentinel_dead_letters WHERE id IN (:a, :b)"
            ),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_b.scalar() or []) == ["exhausted_retries"]


async def test_postgres_rls_enforced_on_ai_cost_usage_batches() -> None:
    """RLS on grc.ai_cost_usage_batches isolates rows by tenant.

    This table is the replay guard for pushed self-hosted usage, and its
    isolation carries a second consequence beyond confidentiality. `batch_id`
    is client-supplied and unique *per tenant*; if a lookup could see across
    tenants, one customer's batch id colliding with another's would make the
    ingest short-circuit as a duplicate and silently drop a real batch of
    spend. So a leak here loses data, not just privacy.

    Mechanics follow test_postgres_rls_enforced_on_sentinel_dead_letters
    exactly: FORCE ROW LEVEL SECURITY because the test role owns the table,
    the NOLOGIN rls_check_role for the SELECT assertions because superusers
    bypass FORCE, the GUC set to each row's own tenant_id before its INSERT
    (FOR ALL with only USING doubles as WITH CHECK), and the nil UUID standing
    in for "unset". See test_postgres_rls_enforced_on_prompt_events for the
    full rationale.

    The counter columns carry Python-side ORM defaults, so `create_all` emits
    no server defaults for them and these raw INSERTs supply them explicitly.
    Migration 043 does declare server defaults; this is a create_all artefact,
    not a schema gap.
    """
    from tests.conftest import TestSessionLocal

    tenant_a = uuid.UUID("00000000-0000-0000-0000-0000000000a4")
    tenant_b = uuid.UUID("00000000-0000-0000-0000-0000000000b4")

    async with TestSessionLocal() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("RLS enforcement requires Postgres")

        await session.execute(text("CREATE SCHEMA IF NOT EXISTS grc"))

        # conftest's create_all builds this from the ORM model, so the DDL
        # no-ops in the normal case. Present for the standalone path.
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS grc.ai_cost_usage_batches (
                    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id      uuid NOT NULL,
                    integration_id uuid NOT NULL,
                    batch_id       varchar(200) NOT NULL,
                    call_count     integer NOT NULL DEFAULT 0,
                    accepted_calls integer NOT NULL DEFAULT 0,
                    rows_touched   integer NOT NULL DEFAULT 0,
                    cost_usd       numeric(14, 6) NOT NULL DEFAULT 0,
                    created_at     timestamptz NOT NULL DEFAULT now(),
                    updated_at     timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )

        await session.execute(
            text("ALTER TABLE grc.ai_cost_usage_batches ENABLE ROW LEVEL SECURITY")
        )
        await session.execute(
            text("ALTER TABLE grc.ai_cost_usage_batches FORCE ROW LEVEL SECURITY")
        )
        await session.execute(
            text("DROP POLICY IF EXISTS tenant_isolation_test ON grc.ai_cost_usage_batches")
        )
        # Byte-identical to migration 043's tenant_isolation policy.
        await session.execute(
            text(
                """
                CREATE POLICY tenant_isolation_test ON grc.ai_cost_usage_batches
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
        await session.execute(text("GRANT SELECT ON grc.ai_cost_usage_batches TO rls_check_role"))
        await session.execute(text("GRANT rls_check_role TO CURRENT_USER"))

        # Batches carry a real FK to grc.integrations. The provider is the one
        # slice 2 auto-provisions on first push.
        integration_a = str(uuid.uuid4())
        integration_b = str(uuid.uuid4())

        async def _seed_integration(integration_id: str, tenant_id: uuid.UUID) -> None:
            await set_tenant_guc(session, tenant_id)
            await session.execute(
                text(
                    """
                    INSERT INTO grc.integrations
                        (id, tenant_id, provider, status, display_name,
                         is_active, is_test_data)
                    VALUES (:id, :tenant_id, 'AWS_BEDROCK', 'CONNECTED',
                            'Self-hosted AI usage', true, true)
                    """
                ),
                {"id": integration_id, "tenant_id": str(tenant_id)},
            )

        await _seed_integration(integration_a, tenant_a)
        await _seed_integration(integration_b, tenant_b)

        # Deliberately the *same* batch_id in both tenants: the per-tenant
        # unique constraint must allow it, and neither tenant may see the
        # other's row under it.
        shared_batch_id = "batch-2026-08-31-0001"
        row_a_id = str(uuid.uuid4())
        row_b_id = str(uuid.uuid4())

        async def _seed_batch(
            row_id: str,
            tenant_id: uuid.UUID,
            integration_id: str,
            accepted_calls: int,
        ) -> None:
            await set_tenant_guc(session, tenant_id)
            await session.execute(
                text(
                    """
                    INSERT INTO grc.ai_cost_usage_batches
                        (id, tenant_id, integration_id, batch_id, call_count,
                         accepted_calls, rows_touched, cost_usd)
                    VALUES (:id, :tenant_id, :integration_id, :batch_id,
                            :accepted_calls, :accepted_calls, 1, 1.500000)
                    """
                ),
                {
                    "id": row_id,
                    "tenant_id": str(tenant_id),
                    "integration_id": integration_id,
                    "batch_id": shared_batch_id,
                    "accepted_calls": accepted_calls,
                },
            )

        await _seed_batch(row_a_id, tenant_a, integration_a, 11)
        await _seed_batch(row_b_id, tenant_b, integration_b, 22)

        await session.execute(
            text(
                "SELECT set_config('app.current_tenant_id',"
                " '00000000-0000-0000-0000-000000000000', true)"
            )
        )
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        # Fail closed on an unmatched GUC.
        res_unset = await session.execute(
            text("SELECT count(*) FROM grc.ai_cost_usage_batches WHERE id IN (:a, :b)"),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_unset.scalar() or 0) == 0

        # A batch_id lookup — the exact shape the replay guard performs — must
        # return only the caller's own row even though both tenants used the
        # same id.
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_a}'"))
        res_a = await session.execute(
            text(
                "SELECT array_agg(accepted_calls) FROM grc.ai_cost_usage_batches "
                "WHERE batch_id = :batch_id"
            ),
            {"batch_id": shared_batch_id},
        )
        assert (res_a.scalar() or []) == [11]

        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_b}'"))
        res_b = await session.execute(
            text(
                "SELECT array_agg(accepted_calls) FROM grc.ai_cost_usage_batches "
                "WHERE batch_id = :batch_id"
            ),
            {"batch_id": shared_batch_id},
        )
        assert (res_b.scalar() or []) == [22]


async def test_postgres_rls_enforced_on_roi_assumptions() -> None:
    """RLS on grc.roi_assumptions isolates rows by tenant.

    This table holds the blended hourly rate and hours-saved figure behind an
    exec-facing ROI headline. A cross-tenant read would do more than leak one
    organisation's cost assumptions to another: `get_or_default_assumptions`
    takes the first row it finds, so another tenant's rate would silently
    become the multiplier in *this* tenant's reported ROI. A wrong headline
    nobody can trace is worse than a missing one.

    Mechanics follow test_postgres_rls_enforced_on_sentinel_dead_letters
    exactly: FORCE ROW LEVEL SECURITY because the test role owns the table,
    the NOLOGIN rls_check_role for the SELECT assertions because superusers
    bypass FORCE, the GUC set to each row's own tenant_id before its INSERT
    (FOR ALL with only USING doubles as WITH CHECK), and the nil UUID standing
    in for "unset". See test_postgres_rls_enforced_on_prompt_events for the
    full rationale.
    """
    from tests.conftest import TestSessionLocal

    tenant_a = uuid.UUID("00000000-0000-0000-0000-0000000000a5")
    tenant_b = uuid.UUID("00000000-0000-0000-0000-0000000000b5")

    async with TestSessionLocal() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("RLS enforcement requires Postgres")

        await session.execute(text("CREATE SCHEMA IF NOT EXISTS grc"))

        # conftest's create_all builds this from the ORM model, so the DDL
        # no-ops in the normal case. `hours_saved_source` is declared as text
        # here to avoid an enum prerequisite on the standalone path.
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS grc.roi_assumptions (
                    id                           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id                    uuid NOT NULL,
                    blended_hourly_rate_usd      numeric(10, 2) NOT NULL DEFAULT 75.00,
                    hours_saved_source           text NOT NULL DEFAULT 'adoption_pipeline',
                    manual_hours_saved_per_month numeric(12, 2),
                    updated_by_user_id           uuid,
                    created_at                   timestamptz NOT NULL DEFAULT now(),
                    updated_at                   timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )

        await session.execute(text("ALTER TABLE grc.roi_assumptions ENABLE ROW LEVEL SECURITY"))
        await session.execute(text("ALTER TABLE grc.roi_assumptions FORCE ROW LEVEL SECURITY"))
        await session.execute(
            text("DROP POLICY IF EXISTS tenant_isolation_test ON grc.roi_assumptions")
        )
        # Byte-identical to migration 044's tenant_isolation policy.
        await session.execute(
            text(
                """
                CREATE POLICY tenant_isolation_test ON grc.roi_assumptions
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
        await session.execute(text("GRANT SELECT ON grc.roi_assumptions TO rls_check_role"))
        await session.execute(text("GRANT rls_check_role TO CURRENT_USER"))

        row_a_id = str(uuid.uuid4())
        row_b_id = str(uuid.uuid4())

        async def _seed(row_id: str, tenant_id: uuid.UUID, rate: str) -> None:
            await set_tenant_guc(session, tenant_id)
            await session.execute(
                text(
                    """
                    INSERT INTO grc.roi_assumptions
                        (id, tenant_id, blended_hourly_rate_usd, hours_saved_source)
                    VALUES (:id, :tenant_id, :rate, 'adoption_pipeline')
                    """
                ),
                {"id": row_id, "tenant_id": str(tenant_id), "rate": rate},
            )

        await _seed(row_a_id, tenant_a, "75.00")
        await _seed(row_b_id, tenant_b, "250.00")

        await session.execute(
            text(
                "SELECT set_config('app.current_tenant_id',"
                " '00000000-0000-0000-0000-000000000000', true)"
            )
        )
        await session.execute(text("SET LOCAL ROLE rls_check_role"))

        # Fail closed on an unmatched GUC.
        res_unset = await session.execute(
            text("SELECT count(*) FROM grc.roi_assumptions WHERE id IN (:a, :b)"),
            {"a": row_a_id, "b": row_b_id},
        )
        assert (res_unset.scalar() or 0) == 0

        # An unfiltered read — the shape `get_or_default_assumptions` performs
        # once RLS is the only thing scoping it — must return only this
        # tenant's rate, never the other's.
        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_a}'"))
        res_a = await session.execute(
            text("SELECT array_agg(blended_hourly_rate_usd) FROM grc.roi_assumptions")
        )
        assert (res_a.scalar() or []) == [Decimal("75.00")]

        await session.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant_b}'"))
        res_b = await session.execute(
            text("SELECT array_agg(blended_hourly_rate_usd) FROM grc.roi_assumptions")
        )
        assert (res_b.scalar() or []) == [Decimal("250.00")]
