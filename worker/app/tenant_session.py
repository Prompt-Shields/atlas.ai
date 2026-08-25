"""Tenant-scoped DB session helper for worker cron jobs.

Background
----------
M0 added a FastAPI dependency `get_tenant_db_session(user)` that sets
`app.current_tenant_id` on each request's DB session so RLS policies
fire correctly. Workers run outside the request context — there is no
`AuthUser` to drive the dependency — so they need their own helper that
takes a tenant_id explicitly.

Pattern for cron jobs that touch tenant-scoped tables:

    from app.models.tenant import Tenant
    from app.database import get_standalone_session
    from app.tenant_session import tenant_scoped_session

    # Get the tenant list with an unrestricted (admin) session
    async with get_standalone_session() as admin_db:
        tenants = (await admin_db.execute(select(Tenant.id))).scalars().all()

    # Iterate per tenant — RLS isolates each block
    for tenant_id in tenants:
        async with tenant_scoped_session(tenant_id) as db:
            result = await db.execute(select(PolicyInstance).where(...))
            for instance in result.scalars():
                ...

Why SET LOCAL
-------------
`SET LOCAL` resets when the transaction ends, which prevents leakage if
the same physical connection later serves another tenant. AsyncSession
auto-begins a transaction on first `execute()`, so the SET LOCAL is
effective for every subsequent query in the block.

Why not bind parameters
-----------------------
PostgreSQL does not support bind parameters in SET statements. We
defensively typecheck `tenant_id` is a `uuid.UUID` (whose stringified
form is constrained to hex + dashes) before string-interpolating into
the SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session_factory


@asynccontextmanager
async def tenant_scoped_session(
    tenant_id: uuid.UUID,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an `AsyncSession` with `app.current_tenant_id` set for RLS.

    Commits on success, rolls back on exception, closes unconditionally
    — matches the contract of `get_standalone_session()` in
    `backend/app/database.py`.
    """
    if not isinstance(tenant_id, uuid.UUID):
        raise TypeError(
            f"tenant_id must be uuid.UUID, got {type(tenant_id).__name__}"
        )

    factory = get_session_factory()
    async with factory() as session:
        try:
            # SET LOCAL does not accept bind parameters, but UUID's
            # canonical str() form is safe to interpolate (hex + dashes
            # only — no SQL metacharacters can survive the typecheck).
            await session.execute(
                text(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = ["tenant_scoped_session"]
