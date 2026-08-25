"""PEP service layer — templates (tenant-agnostic) + instances (issue #237).

Query/mutation logic under `routers/pep.py`. Templates are shared, read-only
reference data (no tenant filter); instances are tenant-scoped, matching
`services/saas_vendor_service.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.policy_evaluator import evaluate_policy
from app.models.policy_enforcement import (
    EnforcementMode,
    PolicyInstance,
    PolicyInstanceStatus,
    PolicyTemplate,
    PolicyViolation,
    RolloutStrategy,
)
from app.schemas.policy_enforcement import (
    PolicyApproveRequest,
    PolicyApproverStatus,
    PolicyDemoteRequest,
    PolicyEligibilityResponse,
    PolicyEvaluateRequest,
    PolicyEvaluateResponse,
    PolicyInstanceCloneRequest,
    PolicyInstanceCreate,
    PolicyInstanceResponse,
    PolicyPromoteRequest,
    PolicyTemplateResponse,
    TriggeredDetector,
)
from app.services.policy_promotion import (
    assess_auto_demote,
    check_promotion_eligibility,
    class_of,
    demote_to_guideline,
    promote_to_strict,
    required_approvers_for,
)

# Mirrors app.services.policy_promotion.AUTO_DEMOTE_DEFAULTS — a fresh
# instance starts with the same watchdog config new dashboard rows get.
_AUTO_DEMOTE_DEFAULTS = {
    "enabled": True,
    "fp_rate_threshold": 5,
    "window_minutes": 60,
    "grace_seconds": 300,
}


def _template_to_response(template: PolicyTemplate) -> PolicyTemplateResponse:
    return PolicyTemplateResponse(
        id=str(template.id),
        slug=template.slug,
        name=template.name,
        version=template.version,
        category=template.category,
        author=template.author,
        owasp_reference=template.owasp_reference,
        regulatory_references=list(template.regulatory_references or []),
        severity=template.severity,
        description=template.description,
        rationale=template.rationale,
        example_violation=template.example_violation,
        example_safe_input=template.example_safe_input,
        triggers=list(template.triggers or []),
        detectors=list(template.detectors or []),
        actions=list(template.actions or []),
        tunable_parameters=list(template.tunable_parameters or []),
        default_enforcement_mode=template.default_enforcement_mode,
        default_applies_to=dict(template.default_applies_to or {}),
        tags=list(template.tags or []),
    )


def _instance_to_response(instance: PolicyInstance) -> PolicyInstanceResponse:
    return PolicyInstanceResponse(
        id=str(instance.id),
        name=instance.name,
        template_id=str(instance.template_id),
        template_version=instance.template_version,
        parameter_values=dict(instance.parameter_values or {}),
        enforcement_mode=instance.enforcement_mode,
        severity=instance.severity,
        applies_to=dict(instance.applies_to or {}),
        allow_list=list(instance.allow_list or []),
        reviewers=list(instance.reviewers or []),
        notifications=dict(instance.notifications or {}),
        status=instance.status,
        created_by_user_id=str(instance.created_by_user_id)
        if instance.created_by_user_id
        else None,
        activated_at=instance.activated_at,
        promotion_history=list(instance.promotion_history or []),
        auto_demote=dict(instance.auto_demote or {}),
        rollout_strategy=instance.rollout_strategy,
        rollout_percentage=instance.rollout_percentage,
        rollout_canary_app_id=(
            str(instance.rollout_canary_app_id) if instance.rollout_canary_app_id else None
        ),
        stats=dict(instance.stats or {}),
    )


# ─── Templates (read-only, tenant-agnostic) ───────────────────────────


async def list_templates(db: AsyncSession) -> list[PolicyTemplateResponse]:
    result = await db.execute(select(PolicyTemplate).order_by(PolicyTemplate.name))
    return [_template_to_response(t) for t in result.scalars().all()]


async def get_template(db: AsyncSession, template_id: uuid.UUID) -> PolicyTemplate | None:
    return await db.get(PolicyTemplate, template_id)


async def get_template_response(
    db: AsyncSession, template_id: uuid.UUID
) -> PolicyTemplateResponse | None:
    template = await get_template(db, template_id)
    return _template_to_response(template) if template else None


# ─── Instances (tenant-scoped) ────────────────────────────────────────


async def list_instances(
    db: AsyncSession, tenant_id: uuid.UUID | None
) -> list[PolicyInstanceResponse]:
    query = select(PolicyInstance).order_by(PolicyInstance.created_at.desc())
    if tenant_id is not None:
        query = query.where(PolicyInstance.tenant_id == tenant_id)
    result = await db.execute(query)
    return [_instance_to_response(i) for i in result.scalars().all()]


async def get_instance(
    db: AsyncSession, tenant_id: uuid.UUID | None, instance_id: uuid.UUID
) -> PolicyInstanceResponse | None:
    query = select(PolicyInstance).where(PolicyInstance.id == instance_id)
    if tenant_id is not None:
        query = query.where(PolicyInstance.tenant_id == tenant_id)
    result = await db.execute(query)
    instance = result.scalar_one_or_none()
    return _instance_to_response(instance) if instance else None


async def create_instance(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    payload: PolicyInstanceCreate,
    *,
    created_by_user_id: uuid.UUID | None,
) -> PolicyInstanceResponse | None:
    """Create an instance directly. Returns None if the template doesn't exist."""
    template = await get_template(db, uuid.UUID(payload.template_id))
    if template is None:
        return None

    instance = PolicyInstance(
        tenant_id=tenant_id,
        name=payload.name,
        template_id=template.id,
        template_version=template.version,
        parameter_values=payload.parameter_values,
        enforcement_mode=payload.enforcement_mode,
        severity=payload.severity,
        applies_to=payload.applies_to,
        allow_list=payload.allow_list,
        reviewers=payload.reviewers,
        notifications=payload.notifications,
        status=payload.status,
        created_by_user_id=created_by_user_id,
        activated_at=datetime.now(UTC),
        auto_demote=dict(_AUTO_DEMOTE_DEFAULTS),
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)
    return _instance_to_response(instance)


async def clone_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    payload: PolicyInstanceCloneRequest,
    *,
    created_by_user_id: uuid.UUID | None,
) -> PolicyInstanceResponse | None:
    """Clone a template into a new tenant instance, in Guideline (log) mode.

    Per `app.services.policy_promotion`'s rule #1, a fresh instance always
    starts Guideline regardless of the template's suggested
    `default_enforcement_mode` — promotion to Strict is a separate, gated
    action (issue #238).
    """
    template = await get_template(db, template_id)
    if template is None:
        return None

    default_parameters = {
        p["key"]: p.get("default")
        for p in (template.tunable_parameters or [])
        if isinstance(p, dict) and "key" in p
    }

    instance = PolicyInstance(
        tenant_id=tenant_id,
        name=payload.name or f"{template.name} (copy)",
        template_id=template.id,
        template_version=template.version,
        parameter_values=(
            payload.parameter_values if payload.parameter_values is not None else default_parameters
        ),
        enforcement_mode=EnforcementMode.log,
        severity=template.severity,
        applies_to=(
            payload.applies_to
            if payload.applies_to is not None
            else dict(template.default_applies_to or {})
        ),
        status=PolicyInstanceStatus.active,
        created_by_user_id=created_by_user_id,
        activated_at=datetime.now(UTC),
        auto_demote=dict(_AUTO_DEMOTE_DEFAULTS),
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)
    return _instance_to_response(instance)


# ─── Lifecycle: evaluate / approve / promote / demote / watchdog (issue #238) ─
#
# `app.agents.policy_evaluator` and `app.services.policy_promotion` are pure
# functions operating on plain dicts (see their module docstrings — they
# shipped before this ORM layer existed). The `_*_to_engine_dict` helpers
# below are the "drop-in bridge" those docstrings anticipated.


def _instance_to_engine_dict(instance: PolicyInstance) -> dict[str, Any]:
    return {
        "id": str(instance.id),
        "template_id": str(instance.template_id),
        "parameter_values": dict(instance.parameter_values or {}),
        "enforcement_mode": EnforcementMode(instance.enforcement_mode).value,
        "status": PolicyInstanceStatus(instance.status).value,
        "activated_at": instance.activated_at.isoformat() if instance.activated_at else None,
        "promotion_history": list(instance.promotion_history or []),
        "stats": dict(instance.stats or {}),
        "auto_demote": dict(instance.auto_demote or {}),
    }


def _template_to_engine_dict(template: PolicyTemplate) -> dict[str, Any]:
    return {
        "id": str(template.id),
        "category": template.category.value,
        "detectors": list(template.detectors or []),
        "triggers": list(template.triggers or []),
        "defaults": {"enforcement_mode": template.default_enforcement_mode.value},
    }


def _eligibility_response(elig: dict[str, Any]) -> PolicyEligibilityResponse:
    return PolicyEligibilityResponse(
        eligible=elig["eligible"],
        days_in_guideline=elig["days_in_guideline"],
        days_required=elig["days_required"],
        false_positive_rate=elig["false_positive_rate"],
        fp_rate_threshold=elig["fp_rate_threshold"],
        approvers=[PolicyApproverStatus(**a) for a in elig["approvers"]],
        blocking_reasons=elig["blocking_reasons"],
    )


async def _load_instance_and_template(
    db: AsyncSession, tenant_id: uuid.UUID | None, instance_id: uuid.UUID
) -> tuple[PolicyInstance, PolicyTemplate] | tuple[None, None]:
    query = select(PolicyInstance).where(PolicyInstance.id == instance_id)
    if tenant_id is not None:
        query = query.where(PolicyInstance.tenant_id == tenant_id)
    instance = (await db.execute(query)).scalar_one_or_none()
    if instance is None:
        return None, None
    template = await db.get(PolicyTemplate, instance.template_id)
    if template is None:
        return None, None
    return instance, template


async def evaluate_instance(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    instance_id: uuid.UUID,
    payload: PolicyEvaluateRequest,
    *,
    evaluated_by_user_id: uuid.UUID | None,
) -> PolicyEvaluateResponse | None:
    """Run the Test Console simulator; write a violation when it would block.

    Returns None if the instance (or its template) isn't found.
    """
    instance, template = await _load_instance_and_template(db, tenant_id, instance_id)
    if instance is None:
        return None

    result = evaluate_policy(
        _instance_to_engine_dict(instance),
        _template_to_engine_dict(template),
        {"prompt": payload.prompt, "expected_output": payload.expected_output},
    )

    now = datetime.now(UTC)
    stats = dict(instance.stats or {})
    stats["total_evaluations_30d"] = int(stats.get("total_evaluations_30d", 0)) + 1
    action = result["action_that_would_fire"]
    if result["matched"]:
        stats["total_hits_30d"] = int(stats.get("total_hits_30d", 0)) + 1
        stats["last_triggered_at"] = now.isoformat()
        if action == "block":
            stats["block_count_30d"] = int(stats.get("block_count_30d", 0)) + 1
        elif action == "flag":
            stats["flag_count_30d"] = int(stats.get("flag_count_30d", 0)) + 1
    instance.stats = stats

    violation_id: str | None = None
    if action == "block":
        violation = PolicyViolation(
            tenant_id=instance.tenant_id,
            policy_instance_id=instance.id,
            prompt_excerpt=payload.prompt[:500],
            action_taken=EnforcementMode.block,
            triggered_detectors=result["triggered_detectors"],
            evaluated_by_user_id=evaluated_by_user_id,
        )
        db.add(violation)
        await db.flush()
        violation_id = str(violation.id)

    await db.commit()

    return PolicyEvaluateResponse(
        matched=result["matched"],
        decision="block" if action == "block" else "allow",
        triggered_detectors=[TriggeredDetector(**d) for d in result["triggered_detectors"]],
        action_that_would_fire=action,
        evaluation_time_ms=result["evaluation_time_ms"],
        violation_id=violation_id,
    )


async def get_eligibility(
    db: AsyncSession, tenant_id: uuid.UUID | None, instance_id: uuid.UUID
) -> PolicyEligibilityResponse | None:
    instance, template = await _load_instance_and_template(db, tenant_id, instance_id)
    if instance is None:
        return None
    elig = check_promotion_eligibility(
        _instance_to_engine_dict(instance), _template_to_engine_dict(template), datetime.now(UTC)
    )
    return _eligibility_response(elig)


async def approve_promotion(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    instance_id: uuid.UUID,
    payload: PolicyApproveRequest,
    *,
    by: str,
) -> PolicyEligibilityResponse | None:
    """Sign off as one of the template's required-approver roles.

    Appends a no-op transition event to `promotion_history` carrying the
    cumulative approver list — `check_promotion_eligibility` reads the
    latest history entry's `approvers`, so this is how sign-offs accumulate
    across multiple `approve` calls without mutating history in place.

    Raises ValueError if the role isn't required for this template or has
    already approved. Returns None if the instance isn't found.
    """
    instance, template = await _load_instance_and_template(db, tenant_id, instance_id)
    if instance is None:
        return None

    instance_dict = _instance_to_engine_dict(instance)
    template_dict = _template_to_engine_dict(template)
    now = datetime.now(UTC)

    required_roles = required_approvers_for(template_dict)
    if payload.role not in required_roles:
        raise ValueError(f"Role '{payload.role}' is not a required approver for this template")

    eligibility = check_promotion_eligibility(instance_dict, template_dict, now)
    approvers = [dict(a) for a in eligibility["approvers"]]
    target = next(a for a in approvers if a["role"] == payload.role)
    if target.get("status") == "approved":
        raise ValueError(f"Role '{payload.role}' has already approved")

    target["status"] = "approved"
    target["approved_by"] = by
    target["approved_at"] = now.isoformat()
    if payload.comment:
        target["comment"] = payload.comment

    current_class = class_of(instance_dict["enforcement_mode"])
    history = list(instance.promotion_history or [])
    history.append(
        {
            "from": current_class,
            "to": current_class,
            "at": now.isoformat(),
            "by": by,
            "reason": f"Approved by {payload.role}",
            "approvers": approvers,
        }
    )
    instance.promotion_history = history
    await db.commit()
    await db.refresh(instance)

    final_eligibility = check_promotion_eligibility(
        _instance_to_engine_dict(instance), template_dict, now
    )
    return _eligibility_response(final_eligibility)


async def promote_instance(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    instance_id: uuid.UUID,
    payload: PolicyPromoteRequest,
    *,
    by: str,
) -> PolicyInstanceResponse | None:
    """Guideline → Strict. Raises ValueError (not eligible / already Strict)."""
    instance, template = await _load_instance_and_template(db, tenant_id, instance_id)
    if instance is None:
        return None

    instance_dict = _instance_to_engine_dict(instance)
    template_dict = _template_to_engine_dict(template)
    now = datetime.now(UTC)

    eligibility = check_promotion_eligibility(instance_dict, template_dict, now)

    result = promote_to_strict(
        instance=instance_dict,
        template=template_dict,
        by=by,
        approvers=eligibility["approvers"],
        rollout_strategy=payload.rollout_strategy.value,
        rollout_canary_app_id=payload.rollout_canary_app_id,
        rollout_percentage=payload.rollout_percentage,
        target_mode=payload.target_mode.value if payload.target_mode else None,
        now=now,
    )
    if not result["ok"]:
        raise ValueError(result["reason"])

    promoted = result["instance"]
    instance.enforcement_mode = EnforcementMode(promoted["enforcement_mode"])
    instance.rollout_strategy = RolloutStrategy(promoted["rollout_strategy"])
    instance.rollout_percentage = promoted.get("rollout_percentage")
    canary = promoted.get("rollout_canary_app_id")
    instance.rollout_canary_app_id = uuid.UUID(canary) if canary else None
    instance.promotion_history = promoted["promotion_history"]

    await db.commit()
    await db.refresh(instance)
    return _instance_to_response(instance)


async def demote_instance(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    instance_id: uuid.UUID,
    payload: PolicyDemoteRequest,
    *,
    by: str,
) -> PolicyInstanceResponse | None:
    """Strict → Guideline. Raises ValueError if not currently Strict."""
    instance, _template = await _load_instance_and_template(db, tenant_id, instance_id)
    if instance is None:
        return None

    instance_dict = _instance_to_engine_dict(instance)
    if class_of(instance_dict["enforcement_mode"]) != "strict":
        raise ValueError("Instance is not in Strict mode")

    now = datetime.now(UTC)
    demoted = demote_to_guideline(instance=instance_dict, by=by, reason=payload.reason, now=now)

    instance.enforcement_mode = EnforcementMode(demoted["enforcement_mode"])
    instance.rollout_strategy = RolloutStrategy(demoted["rollout_strategy"])
    instance.rollout_percentage = demoted.get("rollout_percentage")
    instance.rollout_canary_app_id = None
    instance.promotion_history = demoted["promotion_history"]

    await db.commit()
    await db.refresh(instance)
    return _instance_to_response(instance)


async def run_watchdog_tick_for_tenant(
    db: AsyncSession, tenant_id: uuid.UUID, *, now: datetime
) -> tuple[int, list[dict[str, str]]]:
    """Auto-demote over-threshold Strict instances for one tenant.

    Caller has already set the tenant GUC via `set_tenant_guc`. Returns
    ``(instances_checked, demoted)`` where ``demoted`` is a list of
    ``{"policy_instance_id": str, "reason": str}``, one per instance this
    tick actually demoted.

    Grace-period tracking: the first tick that observes a breach stamps
    ``auto_demote.breach_detected_at`` on the instance; later ticks pass that
    timestamp back into `assess_auto_demote` as ``first_detected_at`` so the
    grace window is measured from first detection, not from "now" every
    tick. A tick that finds the FP rate back under threshold clears the
    stamp. Once demoted, the instance drops out of the Strict scan on the
    next tick — idempotent by construction.
    """
    result = await db.execute(
        select(PolicyInstance).where(
            PolicyInstance.tenant_id == tenant_id,
            PolicyInstance.enforcement_mode.in_([EnforcementMode.block, EnforcementMode.redact]),
        )
    )
    instances = list(result.scalars().all())
    demoted: list[dict[str, str]] = []

    for instance in instances:
        instance_dict = _instance_to_engine_dict(instance)
        config = dict(instance.auto_demote or {})
        first_detected_str = config.get("breach_detected_at")
        first_detected_at = (
            datetime.fromisoformat(first_detected_str) if first_detected_str else None
        )

        assessment = assess_auto_demote(instance_dict, config, first_detected_at, now)

        if assessment["should_demote"]:
            new_state = demote_to_guideline(
                instance=instance_dict, by="watchdog", reason=assessment["reason"], now=now
            )
            instance.enforcement_mode = EnforcementMode(new_state["enforcement_mode"])
            instance.rollout_strategy = RolloutStrategy(new_state["rollout_strategy"])
            instance.rollout_percentage = new_state.get("rollout_percentage")
            instance.rollout_canary_app_id = None
            instance.promotion_history = new_state["promotion_history"]
            config.pop("breach_detected_at", None)
            instance.auto_demote = config
            demoted.append(
                {"policy_instance_id": str(instance.id), "reason": assessment["reason"] or ""}
            )
        elif assessment["grace_until"] is not None and first_detected_at is None:
            config["breach_detected_at"] = now.isoformat()
            instance.auto_demote = config
        elif assessment["grace_until"] is None and first_detected_at is not None:
            config.pop("breach_detected_at", None)
            instance.auto_demote = config

    await db.commit()
    return len(instances), demoted
