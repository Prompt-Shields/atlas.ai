from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingOutbox


async def enqueue(
    db: AsyncSession, *, operation: str, tenant_id: uuid.UUID, payload: dict | None = None
) -> None:
    db.add(BillingOutbox(tenant_id=tenant_id, operation=operation, payload=payload or {}))
    await db.flush()
