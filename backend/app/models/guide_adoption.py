"""Promptly Guide: the connection, and adoption figures by team (promptly-guide #37, #38).

Guide is an employee-facing Mac app whose promise (its requirement E4) is that the
employer learns nothing about one person. Its adoption figures therefore reach
Atlas in a shape that cannot be turned back into a person:

  * **Nothing per person is stored.** A contribution is folded into running
    counts as it arrives: how many people in a team contributed for a month
    (`GuideAdoptionContributors`) and how many of them reached each category
    (`GuideAdoptionCount`). The contribution itself is not kept.
  * **No timestamps on the counts or the receipts.** A receipt written at 10:01:03
    beside a count updated at 10:01:03 would link the two; neither table has a
    time column, so there is nothing to line up.
  * **A receipt only says "someone already contributed for this month"**:
    a keyed hash of the tenant, the person's Firebase subject and the month, and
    nothing about their team or categories. It exists to stop one person counting
    twice. There is no API that reads it.
  * **Figures leave only through the gate** (`guide_adoption_service.report`):
    teams of fewer than ten report nothing, categories reaching fewer than ten are
    suppressed, figures are bands with open ends and there is no team total.

Every Mac sends only after its person opted in (#38: nothing by default).

Tenant-scoped (`tenant_id` + RLS), like `enrolled_devices` and `cost_budgets`:
none of these is an org-level object, and Guide's connection is per tenant.
"""

from __future__ import annotations

from sqlalchemy import JSON, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import GRCBase, TenantScopedMixin, UUIDPrimaryKeyMixin

# The three things Guide's adoption figures may be about (Guide's `AdoptionKind`).
ADOPTION_KINDS = ("topic", "app", "completion")


class GuideConnection(GRCBase, TenantScopedMixin):
    """Which Firebase project (and Identity Platform tenant) is this tenant's Guide,
    and which kinds of figure the tenant offers to count. One per tenant."""

    __tablename__ = "guide_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_guide_connections_tenant"),
        UniqueConstraint(
            "firebase_project_id",
            "firebase_tenant_id",
            name="uq_guide_connections_firebase",
        ),
        {"schema": "grc"},
    )

    firebase_project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # "" when the project does not use Identity Platform tenants. Not NULL, so the
    # unique constraint above holds (Postgres treats NULLs as distinct).
    firebase_tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # Subset of ADOPTION_KINDS. Empty: the tenant counts nothing.
    offered_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class _Untimed(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """No created_at / updated_at, on purpose (see the module docstring)."""

    __abstract__ = True


class GuideAdoptionContributors(_Untimed):
    """How many people in a team contributed for a month."""

    __tablename__ = "guide_adoption_contributors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period", "team", name="uq_guide_adoption_contributors"),
        {"schema": "grc"},
    )

    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-09"
    team: Mapped[str] = mapped_column(String(100), nullable=False)
    people: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GuideAdoptionCount(_Untimed):
    """How many of a team's contributors reached one category in a month."""

    __tablename__ = "guide_adoption_counts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "period",
            "team",
            "category_kind",
            "category_id",
            name="uq_guide_adoption_counts",
        ),
        Index("ix_grc_guide_adoption_counts_period", "tenant_id", "period"),
        {"schema": "grc"},
    )

    period: Mapped[str] = mapped_column(String(7), nullable=False)
    team: Mapped[str] = mapped_column(String(100), nullable=False)
    category_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    category_id: Mapped[str] = mapped_column(String(120), nullable=False)
    people: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GuideAdoptionReceipt(_Untimed):
    """Someone contributed for this month. A keyed hash, and nothing else."""

    __tablename__ = "guide_adoption_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period", "receipt", name="uq_guide_adoption_receipts"),
        {"schema": "grc"},
    )

    period: Mapped[str] = mapped_column(String(7), nullable=False)
    receipt: Mapped[str] = mapped_column(String(64), nullable=False)


__all__ = [
    "ADOPTION_KINDS",
    "GuideAdoptionContributors",
    "GuideAdoptionCount",
    "GuideAdoptionReceipt",
    "GuideConnection",
]
