"""Registered AI use case — the catalogue the survey-bot feeds.

See `docs/use-case-survey-bot.md` for the full design. This module is
PR A of the three-PR backend stack: it owns just the registry CRUD,
not the bot or the survey engine. PR B adds SurveyTemplate /
SurveyDelivery / SurveyResponse; PR C adds the Slack integration.

Each `UseCase` is the formalised description of "team X uses tool Y
for purpose Z." Sources for a registration:

  - `BOT`             — Slack survey response (PR B + C feed this)
  - `FORM`            — atlas /dashboard/registry → /register (M2)
  - `SHADOW_PROMOTE`  — Discover-page promotion of an auto-detected
                        Application record

Status lifecycle:

  DRAFT → REVIEW → ACTIVE → RETIRED

`DRAFT` is the bot-initial state when a response is partial. `REVIEW`
is the IT-lead queue (assign owner, risk-rate, confirm scope).
`ACTIVE` is the published state. `RETIRED` is the soft-delete that
preserves audit history.

The JSON-typed columns (`data_classes`) are stored as Postgres `Text`
holding a JSON string — the convention the rest of the atlas backend
uses (see `RiskMitigation.citations`, `DispatchEvent.payload`). This
keeps the schema portable across the SQLite test fixture and
production Postgres without conditional dialects. The router serialises
on write and `_safe_json_loads` on read.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GRCBase, TenantScopedMixin, TestDataMixin

if TYPE_CHECKING:
    from app.models.saas_vendor import SaaSVendorProfile


class UseCaseStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class UseCaseRiskTier(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class UseCaseSource(str, enum.Enum):
    BOT = "BOT"
    FORM = "FORM"
    SHADOW_PROMOTE = "SHADOW_PROMOTE"
    # Admin bulk-loaded from a CSV upload — see services.ai_inventory_csv.
    # Distinguished from FORM so the audit trail shows whether the
    # registration was admin-asserted (IMPORT) or user-asserted (FORM).
    IMPORT = "IMPORT"
    # Extracted from a played-back agent/employee interview transcript —
    # see services.voice_interview_service (Discover · Voice-interview
    # intake, issue #242).
    VOICE_INTERVIEW = "VOICE_INTERVIEW"


class UseCase(GRCBase, TenantScopedMixin, TestDataMixin):
    """Registered AI use case.

    Tenant-scoped. Created via bot survey, atlas form, or shadow
    promotion. Reviewed and promoted to ACTIVE by IT lead.
    """

    __tablename__ = "use_cases"
    __table_args__ = (
        Index(
            "ix_grc_use_cases_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
        {"schema": "grc"},
    )

    # Core identification
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    tool: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="AI tool name as reported, e.g. 'Microsoft Copilot', 'ChatGPT'",
    )
    department: Mapped[str] = mapped_column(String(255), nullable=False)

    # Owner — nullable so a bot-created Draft can be saved before the
    # IT lead has assigned ownership. SET NULL on user delete to keep
    # the record (audit trail), not cascade.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Lifecycle
    status: Mapped[UseCaseStatus] = mapped_column(
        Enum(UseCaseStatus, schema="grc"),
        nullable=False,
        default=UseCaseStatus.DRAFT,
        index=True,
    )
    risk_tier: Mapped[UseCaseRiskTier] = mapped_column(
        Enum(UseCaseRiskTier, schema="grc"),
        nullable=False,
        default=UseCaseRiskTier.LOW,
    )
    source: Mapped[UseCaseSource] = mapped_column(
        Enum(UseCaseSource, schema="grc"),
        nullable=False,
    )

    # Data sensitivity — JSON array stored as Text. Taxonomy values:
    #   "customer_pii" | "employee_pii" | "phi" | "financial_data" |
    #   "proprietary_code" | "strategy_docs" | "vendor_pii" | ...
    # Use _safe_json_loads in routers (see app.routers.use_cases).
    data_classes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default="[]",
        comment="JSON array of data-class taxonomy strings",
    )

    # Frequency of use — free text by design ("weekly", "ad hoc",
    # "once during grant cycle"). Bot question 5 follow-up writes here.
    frequency: Mapped[str | None] = mapped_column(String(100), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance — when source == BOT, this points at the bot's
    # SurveyResponse row (PR B). Untyped UUID for now — the FK is
    # added in the PR B migration to avoid a forward-reference to a
    # table that doesn't yet exist.
    dispatched_from_response_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "grc.survey_responses.id",
            ondelete="SET NULL",
            name="fk_grc_use_cases_dispatched_from_response_id",
        ),
        nullable=True,
        index=True,
        comment="FK to grc.survey_responses.id (added in PR B)",
    )

    # SaaS Vendor AI (SP-1) — 1:1 vendor profile. Present iff this use_case is a
    # third-party SaaS vendor; the profile's presence is the vendor discriminator.
    saas_vendor_profile: Mapped[SaaSVendorProfile | None] = relationship(
        "SaaSVendorProfile",
        back_populates="use_case",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
