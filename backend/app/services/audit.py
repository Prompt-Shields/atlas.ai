"""Audit logging service."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def log_audit_event(
    db: AsyncSession,
    *,
    event_type: str,
    action: str,
    actor_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    tenant_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    """Write an immutable audit log entry."""
    entry = AuditLog(
        event_type=event_type,
        action=action,
        actor_id=actor_id,
        actor_email=actor_email,
        tenant_id=tenant_id,
        org_id=org_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=json.dumps(details) if details else None,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id,
    )
    db.add(entry)
    await db.flush()
    return entry
