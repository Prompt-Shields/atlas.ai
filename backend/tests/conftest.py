"""
Shared test fixtures for backend test suite.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Environment overrides – must come before app imports
# ---------------------------------------------------------------------------
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production-min-32-chars!")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("SUPER_ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("SUPER_ADMIN_PASSWORD", "TestAdmin_P@ss1")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test-key")
os.environ.setdefault("EMAIL_BACKEND", "console")

# ---------------------------------------------------------------------------
# SQLite UUID affinity shim
# ---------------------------------------------------------------------------
# The ORM declares UUID columns with ``postgresql.UUID``, which renders the
# column type as bare ``UUID`` on SQLite. SQLite gives an unrecognised type
# name NUMERIC affinity, so the character-based bind processor's ``value.hex``
# string gets coerced to an integer whenever the hex is all decimal digits
# (e.g. our deterministic fixtures like ``…000000000010`` → integer ``10``).
# Reading the row back then runs ``uuid.UUID(<int>)`` and raises
# ``AttributeError: 'int' object has no attribute 'replace'``.
#
# Force the SQLite DDL to emit ``CHAR(32)`` (TEXT affinity) so UUID hex always
# round-trips as text. No effect on Postgres, which uses the native UUID type.
from sqlalchemy.dialects.postgresql import UUID as _PGUUID  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app.auth.jwt import create_access_token
from app.database import Base, get_db_session
from app.main import app
from app.models.tenant import Tenant


@compiles(_PGUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
    return "CHAR(32)"


# pgvector's ``Vector`` type has no SQLite compilation. The AI-SPM
# ``ai_assets.embedding`` column is Postgres-only (pgvector extension); on
# SQLite we only need ``create_all`` to succeed, so emit TEXT affinity. Tests
# never exercise vector search on SQLite.
from pgvector.sqlalchemy import Vector as _PGVector  # noqa: E402


@compiles(_PGVector, "sqlite")
def _compile_pgvector_for_sqlite(element, compiler, **kw) -> str:  # noqa: ANN001
    return "TEXT"


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ["DATABASE_URL"],
)

_execution_options = None
_engine_kwargs: dict = {}
# SQLite doesn't support Postgres-style schemas like "grc" and "audit".
# In unit tests we translate those schemas to the default namespace.
if TEST_DATABASE_URL.startswith("sqlite"):
    _execution_options = {"schema_translate_map": {"grc": None, "audit": None}}
else:
    # pytest-asyncio (1.x) runs each test on its own event loop, and asyncpg
    # connections are bound to the loop they were created on.  Pooling would
    # hand a connection created on test 1's loop to test 2 and raise
    # "attached to a different loop".  NullPool gives every checkout a fresh
    # connection on the current loop.  SQLite keeps the default pool: its
    # in-memory ATTACH shim (below) depends on connection reuse.
    from sqlalchemy.pool import NullPool

    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(
    TEST_DATABASE_URL, echo=False, execution_options=_execution_options, **_engine_kwargs
)
TestSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Test-time schema shim — SQLite doesn't understand `grc.` / `audit.` schemas
# but the ORM is hard-coded to use them. Attach in-memory databases under
# those names on every connection so schema-qualified DDL/queries resolve.
# No-op for Postgres connections.
# ---------------------------------------------------------------------------
def _sqlite_attach_schemas(dbapi_conn, _record) -> None:
    """SQLAlchemy `connect` listener: ATTACH grc + audit schemas on SQLite."""
    if not _is_sqlite_connection(dbapi_conn):
        return
    cursor = dbapi_conn.cursor()
    try:
        for schema in ("grc", "audit"):
            try:
                cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
            except Exception:
                # Already attached on this connection.
                pass
    finally:
        cursor.close()


def _is_sqlite_connection(dbapi_conn: object) -> bool:
    cls_name = type(dbapi_conn).__module__
    return "sqlite" in cls_name.lower() or "aiosqlite" in cls_name.lower()


from sqlalchemy import event  # noqa: E402

event.listen(engine.sync_engine, "connect", _sqlite_attach_schemas)


@pytest.fixture(autouse=True)
def _restore_cost_connector_registry() -> Generator[None, None, None]:
    """Snapshot/restore the global cost-connector registry around each test.

    The registry is module-level mutable state; several cost tests register
    fake connectors under real providers. Without this, a fake leaks into
    later tests (e.g. test_connector_is_registered would see the fake).
    """
    from app.services.cost import registry

    snapshot = dict(registry._CONNECTORS)
    yield
    registry._CONNECTORS.clear()
    registry._CONNECTORS.update(snapshot)


@pytest_asyncio.fixture(autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    """Create tables before each test, drop after."""
    async with engine.begin() as conn:
        # Postgres requires the schemas to exist before create_all can
        # CREATE TYPE / CREATE TABLE inside them.  SQLite uses the ATTACH
        # shim (above) and ignores these statements.
        if not TEST_DATABASE_URL.startswith("sqlite"):
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS grc"))
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db_session] = _override_get_db_session


# ---------------------------------------------------------------------------
# HTTP Client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

SUPER_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TENANT_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
VIEWER_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")


@pytest.fixture
def super_admin_token() -> str:
    """JWT for a super-admin user (for endpoint testing)."""
    return create_access_token(
        user_id=SUPER_ADMIN_ID,
        email="admin@test.local",
        roles=["SUPER_ADMIN"],
        tenant_id=SUPER_ADMIN_ID,
        org_id=SUPER_ADMIN_ID,
    )


@pytest.fixture
def tenant_admin_token() -> str:
    return create_access_token(
        user_id=TENANT_ADMIN_ID,
        email="tenantadmin@test.local",
        roles=["TENANT_ADMIN"],
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
    )


@pytest.fixture
def viewer_token() -> str:
    return create_access_token(
        user_id=VIEWER_ID,
        email="viewer@test.local",
        roles=["VIEWER"],
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
    )


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def seeded_principals(setup_database) -> None:
    """Create real rows for the tenants and users the tokens above are minted for.

    `super_admin_token` / `tenant_admin_token` / `viewer_token` only mint a JWT;
    nothing puts the corresponding user in the database. Any endpoint that
    writes the caller's id into an FK column — `integrations.connected_by_user_id`
    is the common one — therefore fails on Postgres while passing on SQLite,
    which does not enforce foreign keys.

    Opt in per module rather than autouse: seeding three extra users globally
    would change the counts that list and aggregate tests assert on.
    """
    from app.models.user import User

    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID, name="Test Tenant")
        await ensure_tenant(session, SUPER_ADMIN_ID, name="Super Admin Tenant")

        for user_id, tenant_id, email, name in (
            (SUPER_ADMIN_ID, SUPER_ADMIN_ID, "admin@test.local", "Super Admin"),
            (TENANT_ADMIN_ID, TEST_TENANT_ID, "tenantadmin@test.local", "Tenant Admin"),
            (VIEWER_ID, TEST_TENANT_ID, "viewer@test.local", "Viewer"),
        ):
            if await session.get(User, user_id) is not None:
                continue
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email,
                    full_name=name,
                    hashed_password="placeholder",
                    is_active=True,
                    is_email_verified=True,
                    is_test_data=True,
                )
            )
        await session.commit()


# ---------------------------------------------------------------------------
# Tenant seeding
# ---------------------------------------------------------------------------


async def ensure_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    name: str = "Test Tenant",
    slug: str | None = None,
) -> Tenant:
    """Insert a `tenants` row for `tenant_id` unless one already exists.

    Many tenant-scoped tables carry a FK to `grc.tenants`. SQLite does not
    enforce foreign keys by default, so a test that seeds rows for a tenant it
    never created passes locally and dies on Postgres with a
    ForeignKeyViolationError. Call this before seeding anything tenant-scoped.

    `slug` is derived from the full id hex because `uq_tenants_slug` is unique.
    A truncated hex is not enough: the conftest ids are all-zeros apart from
    their final digits, so any prefix of them collides.
    """
    existing = await session.get(Tenant, tenant_id)
    if existing is not None:
        return existing

    tenant = Tenant(
        id=tenant_id,
        name=name,
        slug=slug or f"test-{tenant_id.hex}",
        is_active=True,
        is_test_data=True,
    )
    session.add(tenant)
    await session.flush()
    return tenant


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_client() -> MagicMock:
    client = MagicMock()
    client.complete = AsyncMock(
        return_value={
            "content": '{"risks": [], "mitigations": []}',
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
    )
    return client
