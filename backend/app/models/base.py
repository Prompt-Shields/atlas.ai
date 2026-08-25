"""Shared model mixins and base classes."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TimestampMixin:
    """Adds created_at and updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """UUID primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.uuid_generate_v4(),
    )


class TenantScopedMixin:
    """Mixin for tenant-scoped tables."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )


class OrgScopedMixin(TenantScopedMixin):
    """Mixin for org-scoped tables (inherits tenant scope)."""

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )


class TestDataMixin:
    """Marks records as test data for safe cleanup."""

    is_test_data: Mapped[bool] = mapped_column(default=False, nullable=False)


class GRCBase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Abstract base for all GRC domain models."""

    __abstract__ = True
    __table_args__ = {"schema": "grc"}
