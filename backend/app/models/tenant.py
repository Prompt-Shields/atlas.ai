"""Tenant and Organisation models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GRCBase, TestDataMixin


class Plan(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class Tenant(GRCBase, TestDataMixin):
    __tablename__ = "tenants"
    # Named UNIQUE constraint rather than a unique index — see User.email.
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
        {"schema": "grc"},
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # uq_tenants_slug is backed by a unique index, so slug is already
    # indexed; index=True here would just add a second one.
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Onboarding journey state (PR for integrations + onboarding).
    # The /onboarding wizard walks new tenants through Microsoft Entra
    # ID and Slack connections; setting onboarding_completed_at exits
    # the wizard on subsequent logins.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Entra ID directory (`tid` claim) this tenant self-provisioned from, set
    # when the first user of an Azure AD tenant signs up via SSO self-serve
    # (SP-Azure). NULL for tenants created by password signup. Unique so a
    # given Azure directory maps to at most one Atlas tenant — later users
    # from the same `tid` join this tenant rather than creating a new one.
    azure_tenant_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )

    # Billing columns (added by 020_billing_v0 migration)
    plan: Mapped[Plan] = mapped_column(
        Enum(Plan, name="plan", schema="grc", values_callable=lambda e: [m.value for m in e]),
        default=Plan.FREE,
        nullable=False,
    )
    seats_billed: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[SubscriptionStatus | None] = mapped_column(
        Enum(
            SubscriptionStatus,
            name="subscription_status",
            schema="grc",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    plan_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    organisations: Mapped[list[Organisation]] = relationship(
        "Organisation", back_populates="tenant", cascade="all, delete-orphan"
    )


class Organisation(GRCBase, TestDataMixin):
    __tablename__ = "organisations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_organisations_tenant_slug"),
        {"schema": "grc"},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="organisations")
