"""User, Role, and API Key models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GRCBase, TestDataMixin


class Role(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    TENANT_ADMIN = "TENANT_ADMIN"
    ORG_ADMIN = "ORG_ADMIN"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"


class User(GRCBase, TestDataMixin):
    __tablename__ = "users"
    # Uniqueness is a named constraint, with a separate plain lookup index —
    # matching what migration 001 built. Declaring `unique=True` on the column
    # instead would ask for a unique *index* and show up as schema drift.
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_email", "email"),
        {"schema": "grc"},
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Nullable: SSO-only users (e.g. Entra ID) authenticate without a local password.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Entra ID object id (`oid`) linking this user to an external identity.
    # NULL for local email/password users; unique across SSO-provisioned users.
    entra_object_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Billing / activity timestamps (added by 020_billing_v0 migration)
    first_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_prompt_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )

    # Tenant/Org scoping (nullable for SUPER_ADMIN)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.organisations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    roles: Mapped[list[UserRole]] = relationship(
        "UserRole", back_populates="user", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list[APIKey]] = relationship(
        "APIKey", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def role_names(self) -> list[Role]:
        return [ur.role for ur in self.roles]

    @property
    def highest_role(self) -> Role:
        priority = [Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.ORG_ADMIN, Role.ANALYST, Role.VIEWER]
        for role in priority:
            if role in self.role_names:
                return role
        return Role.VIEWER


class UserRole(GRCBase):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_user_roles_user_role"),
        {"schema": "grc"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[Role] = mapped_column(Enum(Role, schema="grc"), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.organisations.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="roles")


class APIKey(GRCBase, TestDataMixin):
    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    hashed_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="api_keys")
