"""Pydantic schemas for the PEP router (`/api/v1/pep`, issue #237).

Wire format mirrors the ORM field names (snake_case), matching every other
atlas router. Enum *values* mirror the ORM enums exactly, so the wire
contract stays byte-for-byte compatible with
`app.services.policy_promotion` / `app.agents.policy_evaluator` (M2.5, issue
#238), which already speak this dict shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.policy_enforcement import (
    EnforcementMode,
    PolicyCategory,
    PolicyInstanceStatus,
    PolicySeverity,
    RolloutStrategy,
)


class PolicyTemplateResponse(BaseModel):
    """A shipped, read-only template — identical across every tenant."""

    id: str
    slug: str
    name: str
    version: str
    category: PolicyCategory
    author: str

    owasp_reference: str | None = None
    regulatory_references: list[str] = Field(default_factory=list)
    severity: PolicySeverity

    description: str
    rationale: str
    example_violation: str
    example_safe_input: str | None = None

    triggers: list[dict[str, Any]] = Field(default_factory=list)
    detectors: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    tunable_parameters: list[dict[str, Any]] = Field(default_factory=list)

    default_enforcement_mode: EnforcementMode
    default_applies_to: dict[str, Any] = Field(default_factory=dict)

    tags: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class PolicyInstanceCreate(BaseModel):
    """Create an instance directly (full control over every field)."""

    name: str = Field(min_length=1)
    template_id: str
    parameter_values: dict[str, Any] = Field(default_factory=dict)
    enforcement_mode: EnforcementMode = EnforcementMode.log
    severity: PolicySeverity
    applies_to: dict[str, Any] = Field(default_factory=dict)
    allow_list: list[str] = Field(default_factory=list)
    reviewers: list[str] = Field(default_factory=list)
    notifications: dict[str, Any] = Field(default_factory=dict)
    status: PolicyInstanceStatus = PolicyInstanceStatus.active


class PolicyInstanceCloneRequest(BaseModel):
    """Clone a template into a new tenant instance.

    Every field is an override on top of the template's defaults — omit to
    inherit. ``enforcement_mode`` is deliberately not overridable here: a
    clone always starts Guideline (``log``); promotion to Strict is a
    separate, gated action (issue #238).
    """

    name: str | None = None
    applies_to: dict[str, Any] | None = None
    parameter_values: dict[str, Any] | None = None


class PolicyApproverStatus(BaseModel):
    """One required-approver role's sign-off state (see `policy_promotion.check_promotion_eligibility`)."""

    role: str
    status: str = "pending"
    approved_by: str | None = None
    approved_at: str | None = None


class PolicyEligibilityResponse(BaseModel):
    """Whether a Guideline instance may promote to Strict right now."""

    eligible: bool
    days_in_guideline: int
    days_required: int
    false_positive_rate: float
    fp_rate_threshold: float
    approvers: list[PolicyApproverStatus] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)


class PolicyApproveRequest(BaseModel):
    """Sign off, as one of the template's required-approver roles."""

    role: str = Field(min_length=1)
    comment: str | None = None


class PolicyPromoteRequest(BaseModel):
    """Attempt Guideline → Strict. Rejected (400) unless eligibility is met."""

    rollout_strategy: RolloutStrategy = RolloutStrategy.all
    rollout_canary_app_id: str | None = None
    rollout_percentage: int | None = None
    target_mode: EnforcementMode | None = None


class PolicyDemoteRequest(BaseModel):
    """Strict → Guideline. Always permitted — the safety valve."""

    reason: str | None = None


class PolicyEvaluateRequest(BaseModel):
    """Run the template's detectors against a sample prompt (Test Console)."""

    prompt: str = Field(min_length=1)
    expected_output: str | None = None


class TriggeredDetector(BaseModel):
    detector_id: str
    confidence: float
    explanation: str
    matched_substring: str | None = None


class PolicyEvaluateResponse(BaseModel):
    """Result of one simulator run. ``decision`` is the binary allow/block
    outcome; the richer ``action_that_would_fire`` distinguishes log/flag/
    redact/block/none for the Test Console UI."""

    matched: bool
    decision: str  # "allow" | "block"
    triggered_detectors: list[TriggeredDetector] = Field(default_factory=list)
    action_that_would_fire: str
    evaluation_time_ms: int
    violation_id: str | None = None


class WatchdogDemotedItem(BaseModel):
    tenant_id: str
    policy_instance_id: str
    reason: str


class WatchdogTickResponse(BaseModel):
    """Outcome of one all-tenant auto-demote sweep."""

    instances_checked: int
    demoted: list[WatchdogDemotedItem] = Field(default_factory=list)


class PolicyInstanceResponse(BaseModel):
    id: str
    name: str
    template_id: str
    template_version: str

    parameter_values: dict[str, Any]
    enforcement_mode: EnforcementMode
    severity: PolicySeverity
    applies_to: dict[str, Any]

    allow_list: list[str]
    reviewers: list[str]
    notifications: dict[str, Any]

    status: PolicyInstanceStatus
    created_by_user_id: str | None = None
    activated_at: datetime | None = None

    promotion_history: list[dict[str, Any]]
    auto_demote: dict[str, Any]
    rollout_strategy: RolloutStrategy
    rollout_percentage: int | None = None
    rollout_canary_app_id: str | None = None

    stats: dict[str, Any]

    class Config:
        from_attributes = True
