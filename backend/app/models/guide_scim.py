"""SCIM provisioning for Promptly Guide (promptly-guide #58, E10).

The customer's identity provider (Entra ID) pushes users and groups here over SCIM 2.0,
and Guide asks which groups the signed-in person is in, for group-based enablement:
which of the organisation's documents Guide answers from.

Kept to what that needs, and no more:

  * **A user is a userName, an externalId and active.** Entra sends names, emails, job
    titles, managers and phone numbers too; none of it is stored. The userName is what
    Guide's sign-in is matched on.
  * **A group is a display name, an externalId and its members.**
  * **Nothing about use.** These tables say who is in which group, as the customer's
    directory already does; nothing Guide does is joined to them.

Separate from `directory_*` (the Microsoft Graph pull): those keep a richer roster for
Atlas's own features, under an integration. This is the push, under a SCIM token, and
deliberately thinner.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class GuideScimToken(GRCBase, TenantScopedMixin):
    """The bearer token the tenant's identity provider presents. Only its sha256 is
    kept; the raw token is shown once, when it is made. One per tenant."""

    __tablename__ = "guide_scim_tokens"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_guide_scim_tokens_tenant"),
        Index("ix_grc_guide_scim_tokens_hash", "token_hash", unique=True),
        {"schema": "grc"},
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class GuideScimUser(GRCBase, TenantScopedMixin):
    __tablename__ = "guide_scim_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_name_key", name="uq_guide_scim_users_name"),
        {"schema": "grc"},
    )

    # As the identity provider sent it; usually the person's UPN.
    user_name: Mapped[str] = mapped_column(String(320), nullable=False)
    # Lowercased, for uniqueness and for matching a sign-in (SCIM userName is
    # case-insensitive, RFC 7643 §4.1.1).
    user_name_key: Mapped[str] = mapped_column(String(320), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class GuideScimGroup(GRCBase, TenantScopedMixin):
    __tablename__ = "guide_scim_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "display_name", name="uq_guide_scim_groups_name"),
        {"schema": "grc"},
    )

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class GuideScimMember(GRCBase, TenantScopedMixin):
    __tablename__ = "guide_scim_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_guide_scim_members"),
        {"schema": "grc"},
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.guide_scim_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.guide_scim_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
