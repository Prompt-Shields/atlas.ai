"""LLM Policy Enforcement Point — template catalog + tenant instances.

M2 of the AISPM port (issue #237). Ports `lib/policy-templates/*` (template +
instance type system) from `ai-spm-dashboard`. Distinct from the atlas
device-policy domain (`routers/mdm_connect.py` + friends) — this is the
LLM-prompt PEP: reusable, tenant-agnostic ``PolicyTemplate`` rows (OWASP_LLM,
EU_AI_ACT, GDPR, INDUSTRY, SHADOW_AI, CONTENT_SAFETY, CUSTOM) that tenants
clone into their own ``PolicyInstance`` rows.

Field/enum names and the enforcement-mode vocabulary (``log``/``flag``/
``block``/``redact``) match `app.services.policy_promotion` and
`app.agents.policy_evaluator` — the M2.5 pure-function ports that already
operate on this exact dict shape — so M2.5's ``.model_dump()`` /
``model_validate()`` bridge (see their module docstrings) is a drop-in once
this lands. ``PolicyCategory`` values are the same upper-snake strings the
promotion service's ``_REQUIRED_APPROVERS_BY_CATEGORY`` table already keys on.

``PolicyTemplate`` carries no ``tenant_id`` — it is shipped, read-only
reference data seeded once by the migration, not RLS-scoped.
``PolicyInstance`` is tenant-scoped (``TenantScopedMixin`` + RLS), matching
`app.models.saas_vendor`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin

# JSONB on PostgreSQL (GIN-indexable), plain JSON on SQLite (test suite).
_JSONBColumn = JSON().with_variant(JSONB(), "postgresql")


class PolicyCategory(str, enum.Enum):
    owasp_llm = "OWASP_LLM"
    eu_ai_act = "EU_AI_ACT"
    gdpr = "GDPR"
    industry = "INDUSTRY"
    shadow_ai = "SHADOW_AI"
    content_safety = "CONTENT_SAFETY"
    custom = "CUSTOM"


class PolicySeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class EnforcementMode(str, enum.Enum):
    log = "log"
    flag = "flag"
    block = "block"
    redact = "redact"


class PolicyInstanceStatus(str, enum.Enum):
    draft = "draft"
    testing = "testing"
    active = "active"
    paused = "paused"
    archived = "archived"


class RolloutStrategy(str, enum.Enum):
    all = "all"
    canary = "canary"
    phased = "phased"


class PolicyTemplate(GRCBase):
    """A shipped, read-only policy definition. Tenants clone this into instances.

    No tenant scoping — the catalog is identical for every tenant. ``slug``
    is a stable human-readable key (e.g. ``"owasp-llm01-prompt-injection"``)
    so seed data and future re-seeding stay idempotent even though the PK is
    a generated UUID.
    """

    __tablename__ = "policy_templates"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_policy_template_slug"),
        {"schema": "grc"},
    )

    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    category: Mapped[PolicyCategory] = mapped_column(
        Enum(PolicyCategory, schema="grc", name="policy_category"), nullable=False, index=True
    )
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="atlas.ai")

    owasp_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    regulatory_references: Mapped[list[str]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    severity: Mapped[PolicySeverity] = mapped_column(
        Enum(PolicySeverity, schema="grc", name="policy_severity"), nullable=False
    )

    description: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    example_violation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    example_safe_input: Mapped[str | None] = mapped_column(Text, nullable=True)

    # list[{"stage": TriggerStage, "description": str}]
    triggers: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    # list[{"id", "type", "description", "config_ref"}]
    detectors: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    # list[{"type", "description", "config_ref"}]
    actions: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    # list[{"key", "label", "type", "default", "helpText", "level", ...}]
    tunable_parameters: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )

    default_enforcement_mode: Mapped[EnforcementMode] = mapped_column(
        Enum(EnforcementMode, schema="grc", name="policy_enforcement_mode"),
        nullable=False,
        default=EnforcementMode.log,
    )
    # {"data_classifications": [...], "risk_tiers": [...], "departments": [...]}
    default_applies_to: Mapped[dict[str, Any]] = mapped_column(
        _JSONBColumn, nullable=False, default=dict
    )

    tags: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)


class PolicyInstance(GRCBase, TenantScopedMixin):
    """A tenant-owned policy, cloned from a template and independently tuned.

    New instances always start Guideline (``enforcement_mode="log"``,
    regardless of the template's suggested default) — promotion to Strict is
    a deliberate, gated action handled by `app.services.policy_promotion`
    (M2.5, issue #238), not something clone grants for free.
    """

    __tablename__ = "policy_instances"
    __table_args__ = ({"schema": "grc"},)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.policy_templates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Captured at clone time — used for upgrade-drift detection later.
    template_version: Mapped[str] = mapped_column(String(50), nullable=False)

    parameter_values: Mapped[dict[str, Any]] = mapped_column(
        _JSONBColumn, nullable=False, default=dict
    )
    enforcement_mode: Mapped[EnforcementMode] = mapped_column(
        Enum(EnforcementMode, schema="grc", name="policy_enforcement_mode"),
        nullable=False,
        default=EnforcementMode.log,
    )
    severity: Mapped[PolicySeverity] = mapped_column(
        Enum(PolicySeverity, schema="grc", name="policy_severity"), nullable=False
    )
    # {"application_ids": [...], "data_classifications": [...], "risk_tiers": [...], "departments": [...]}
    applies_to: Mapped[dict[str, Any]] = mapped_column(_JSONBColumn, nullable=False, default=dict)

    allow_list: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    reviewers: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    # {"on_block": [...], "on_flag": [...], "on_log": [...]}
    notifications: Mapped[dict[str, Any]] = mapped_column(
        _JSONBColumn, nullable=False, default=dict
    )

    status: Mapped[PolicyInstanceStatus] = mapped_column(
        Enum(PolicyInstanceStatus, schema="grc", name="policy_instance_status"),
        nullable=False,
        default=PolicyInstanceStatus.active,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # list[{"from", "to", "at", "by", "reason", "approvers"}] — append-only.
    promotion_history: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    # {"enabled", "fp_rate_threshold", "window_minutes", "grace_seconds"}
    auto_demote: Mapped[dict[str, Any]] = mapped_column(_JSONBColumn, nullable=False, default=dict)
    rollout_strategy: Mapped[RolloutStrategy] = mapped_column(
        Enum(RolloutStrategy, schema="grc", name="policy_rollout_strategy"),
        nullable=False,
        default=RolloutStrategy.all,
    )
    rollout_percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rollout_canary_app_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # {"total_evaluations_30d", "total_hits_30d", "block_count_30d", "flag_count_30d",
    #  "last_triggered_at", "false_positives_30d", "false_positive_rate"}
    stats: Mapped[dict[str, Any]] = mapped_column(_JSONBColumn, nullable=False, default=dict)


class PolicyViolation(GRCBase, TenantScopedMixin):
    """A recorded hit from `POST /pep/policies/{id}/evaluate` (issue #238).

    Written whenever an evaluate call would have blocked the prompt
    (``action_that_would_fire == "block"``), giving the promotion-eligibility
    gate and the auto-demote watchdog real signal beyond the instance's
    rolling ``stats`` counters.
    """

    __tablename__ = "policy_violations"
    __table_args__ = ({"schema": "grc"},)

    policy_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.policy_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    action_taken: Mapped[EnforcementMode] = mapped_column(
        Enum(EnforcementMode, schema="grc", name="policy_enforcement_mode"), nullable=False
    )
    # list[{"detector_id", "confidence", "explanation", "matched_substring"?}]
    triggered_detectors: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    evaluated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
