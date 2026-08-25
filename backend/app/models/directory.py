"""Directory users + groups — Microsoft Graph (and future Workspace) sync.

When an Entra ID integration is connected, the `microsoft_sync` worker
pulls the org's user roster from Microsoft Graph and upserts rows
here. Survey audience filters resolve against this table when a
directory integration is present (`mode=departments` queries
DirectoryUser by department); they fall back to the local `User`
table when no directory is connected (the PR #29 behaviour).

Why a separate table from `User`:
  - `User` is for atlas login identities; tens of admins per tenant.
  - `DirectoryUser` is the org's full roster; potentially thousands
    per tenant. Most never log in to atlas.
  - Department / job-title metadata lives here because the source of
    truth is Microsoft Graph, not atlas's onboarding form.

Mapping to `User`: when a DirectoryUser logs in via SSO, the atlas
auth flow creates a corresponding `User` row and links via email.
Out of scope for this PR; the join column `linked_user_id` is added
now so it's there when SSO ships.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin


class DirectoryUser(GRCBase, TenantScopedMixin, TestDataMixin):
    """One row per Microsoft Graph user, per tenant.

    Upserted by the `microsoft_sync` worker. Unique on
    `(tenant_id, integration_id, external_user_id)` because multiple
    Entra integrations *could* point at the same Microsoft tenant
    (rare; we model it correctly anyway).
    """

    __tablename__ = "directory_users"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_user_id",
            name="uq_grc_directory_users_tenant_integration_external",
        ),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Microsoft Graph user id (objectId in classic AAD).
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        index=True,
        comment="userPrincipalName or mail — lowercased",
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="From Graph `department`; survey audience filters by this",
    )
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Entra `manager` object id. Direct reports are derivable (users whose
    # manager_external_user_id == this row's external_user_id), so no extra table.
    manager_external_user_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # `accountEnabled` from Graph — disabled users excluded from
    # default audience resolution.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Optional link to an atlas User (set when the user logs in via SSO).
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DirectoryGroup(GRCBase, TenantScopedMixin, TestDataMixin):
    """Microsoft Graph group / security group / M365 group.

    v0.1 scope: only the metadata + member count. Membership join
    table arrives in a follow-up if we need fine-grained audience
    targeting (e.g. "send survey to this Teams channel's members").
    """

    __tablename__ = "directory_groups"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_group_id",
            name="uq_grc_directory_groups_tenant_integration_external",
        ),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    external_group_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    group_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Security | Microsoft365 | Distribution",
    )
    member_count: Mapped[int] = mapped_column(default=0, nullable=False)

    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DirectoryGroupMembership(GRCBase, TenantScopedMixin, TestDataMixin):
    """Join row: a DirectoryUser belongs to a DirectoryGroup.

    Upserted by the `microsoft_sync` worker from Graph
    `/groups/{id}/members`. The DirectoryGroup docstring flagged this
    table as the follow-up needed for fine-grained audience targeting
    and the admin portal's team-membership view (PRO-56).
    """

    __tablename__ = "directory_group_memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_grc_dir_group_membership"),
        {"schema": "grc"},
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.directory_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.directory_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
