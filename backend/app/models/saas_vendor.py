"""SaaS Vendor AI — third-party / supply-chain AI risk.

SP-1 of the ai-spm-dashboard port (see `docs/` + epic #187). A "SaaS vendor"
is a `grc.use_cases` row that also carries a 1:1 `saas_vendor_profiles` row —
the profile's PRESENCE is the discriminator, so the AI-inventory (use_cases)
model gains no columns and its aggregates are unaffected (they anti-join on the
profile to exclude vendors). See migration `026_saas_vendor_ai`.

Ported from `ai-spm-dashboard` `lib/vendor-types.ts`. Reuses the shared Risk
model for risks/mitigations and `UseCase.owner_user_id` / `UseCase.source` for
owner + provenance; the vendor-only fields live here.

Assessment exchange (net-new, not in AISPM):
  - VendorAssessmentRequest — outbound "request assessment"
  - VendorAssessmentImport  — inbound "import assessment" (trust-center / peer)
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GRCBase, TenantScopedMixin

if TYPE_CHECKING:
    from app.models.use_case import UseCase

# JSONB on PostgreSQL (GIN-indexable), falls back to JSON on SQLite (test suite).
_JSONBColumn = JSON().with_variant(JSONB(), "postgresql")


class VendorCategory(str, enum.Enum):
    llm_provider = "llm_provider"
    productivity_suite = "productivity_suite"
    developer_tool = "developer_tool"
    crm_support = "crm_support"
    data_analytics = "data_analytics"


class VendorDiscoveryMethod(str, enum.Enum):
    endpoint = "endpoint"
    browser_extension = "browser_extension"
    sso = "sso"
    integration = "integration"
    manual = "manual"


class VendorContractStatus(str, enum.Enum):
    active = "active"
    pending_renewal = "pending_renewal"
    expired = "expired"


class AssessmentStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    received = "received"
    reviewed = "reviewed"


class AssessmentImportSource(str, enum.Enum):
    peer_shared = "peer_shared"
    trust_center = "trust_center"
    upload = "upload"


class SaaSVendorProfile(GRCBase, TenantScopedMixin):
    """Vendor-only fields, 1:1 with a `use_cases` row (its presence = "is a vendor")."""

    __tablename__ = "saas_vendor_profiles"
    __table_args__ = (
        Index("ix_grc_saas_vendor_profiles_tenant_risk", "tenant_id", "risk_score"),
        UniqueConstraint("use_case_id", name="uq_saas_vendor_profile_use_case"),
        # Bare name — the metadata naming_convention prefixes "ck" constraints
        # with the table name, yielding "ck_saas_vendor_profiles_risk_score_range".
        # Migration 026 declares that exact name so create_all (tests) and the
        # migration (prod) agree.
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="risk_score_range",
        ),
        {"schema": "grc"},
    )

    use_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.use_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[VendorCategory] = mapped_column(
        Enum(VendorCategory, schema="grc", name="vendor_category"),
        nullable=False,
    )
    discovered_via: Mapped[VendorDiscoveryMethod] = mapped_column(
        Enum(VendorDiscoveryMethod, schema="grc", name="vendor_discovery_method"),
        nullable=False,
        default=VendorDiscoveryMethod.manual,
    )
    # 0–100; higher is riskier (CHECK enforced above).
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Foundation models behind the AI feature (list[str]).
    models: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    # Fourth parties the vendor routes data through (list[str]).
    sub_processors: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    # list[{"description", "classification", "trains_on_data"}].
    data_flows: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    certifications: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    # {"euAiAct", "nistAiRmf", "owaspLlm", "iso42001"} → covered|partial|gap.
    compliance_status: Mapped[dict[str, Any]] = mapped_column(
        _JSONBColumn, nullable=False, default=dict
    )
    dpa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    training_opt_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contract_status: Mapped[VendorContractStatus] = mapped_column(
        Enum(VendorContractStatus, schema="grc", name="vendor_contract_status"),
        nullable=False,
        default=VendorContractStatus.active,
    )
    last_reviewed_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    use_case: Mapped[UseCase] = relationship("UseCase", back_populates="saas_vendor_profile")


class VendorAssessmentRequest(GRCBase, TenantScopedMixin):
    """Outbound 'request assessment' — in-app simulated send (no real email in SP-1)."""

    __tablename__ = "vendor_assessment_requests"
    __table_args__ = (
        Index("ix_grc_vendor_assess_req_tenant_vendor", "tenant_id", "use_case_id"),
        {"schema": "grc"},
    )

    use_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.use_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    questionnaire: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[AssessmentStatus] = mapped_column(
        Enum(AssessmentStatus, schema="grc", name="assessment_status"),
        nullable=False,
        default=AssessmentStatus.draft,
    )
    token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class VendorAssessmentImport(GRCBase, TenantScopedMixin):
    """Inbound 'import assessment' — peer-shared / trust-center / uploaded."""

    __tablename__ = "vendor_assessment_imports"
    __table_args__ = (
        Index("ix_grc_vendor_assess_imp_tenant_vendor", "tenant_id", "use_case_id"),
        {"schema": "grc"},
    )

    use_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.use_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[AssessmentImportSource] = mapped_column(
        Enum(AssessmentImportSource, schema="grc", name="assessment_import_source"),
        nullable=False,
    )
    reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(_JSONBColumn, nullable=False, default=dict)
    imported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
