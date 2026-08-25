"""Policy promotion lifecycle: Guideline → Strict (and back).

Pure-function port of the dashboard's `lib/policy-engine/promotion.ts`.
Behaviour-equivalent to that source file: eligibility check, promotion,
demotion, auto-demote watchdog, risk preview, per-category approver
matrix.

Rules (from the source):

1. New instances always start as Guideline regardless of template
   default. Templates only suggest the *target* mode; admins decide
   when to flip.
2. Promotion to Strict requires ALL of:
   - N days in Guideline mode (default 14)
   - FP rate < threshold (default 2%)
   - Required approvers signed off (per template category)
3. Demotion (Strict → Guideline) is always permitted, no approvals.
4. Auto-demote watchdog fires when live FP rate exceeds configured cap.

Design choices (same approach as `policy_evaluator.py`):

- **Dict-based interface, no Pydantic dependency.** Ships before M1
  lands the ORM models / Pydantic schemas. Caller passes plain dicts in
  (matching the camelCase wire format), gets plain dicts back. When M1
  lands, wrap with typed helpers that do `.model_dump()` on the way in
  and `model_validate()` on the way out.
- **camelCase input keys preferred for inputs** (`enforcementMode`,
  `parameterValues`, `promotionHistory`, `falsePositiveRate`,
  `activatedAt`) since this module sits between the API layer and the
  evaluator, both of which speak the wire format.
- **Output keys are snake_case** for Python ergonomics. Cursor's M2.5
  endpoint converts to the wire format via Pydantic alias on the way out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# ─── Tunables (could be loaded from admin config) ────────────────────

PROMOTION_DEFAULTS: dict[str, Any] = {
    "days_in_guideline_required": 14,
    "fp_rate_threshold": 0.02,  # 2%
}

AUTO_DEMOTE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "fp_rate_threshold": 5,  # 5% — much looser than promotion gate
    "window_minutes": 60,
    "grace_seconds": 300,
}

# ─── Required approvers per template category ────────────────────────
#
# Higher-risk regulatory categories require sign-off from specific roles.
# This is the second gate beyond time-in-mode and FP rate.

_REQUIRED_APPROVERS_BY_CATEGORY: dict[str, list[str]] = {
    "GDPR": ["DPO"],
    "EU_AI_ACT": ["DPO", "Security Lead"],
    "INDUSTRY": ["Security Lead", "Compliance Officer"],  # PCI / HIPAA / Legal
    "OWASP_LLM": ["Security Lead"],
    "SHADOW_AI": ["Security Lead"],
    "CONTENT_SAFETY": ["Trust & Safety Lead"],
    "CUSTOM": ["Policy Author"],
}


def required_approvers_for(template: dict[str, Any]) -> list[str]:
    """Return the role tags that must approve before this template's
    instances can be promoted to Strict."""
    return _REQUIRED_APPROVERS_BY_CATEGORY.get(template.get("category", ""), ["Security Lead"])


# ─── Policy class helpers ────────────────────────────────────────────


def class_of(enforcement_mode: str) -> str:
    """Derive the policy class from the enforcement mode.

    Strict modes alter traffic; guideline modes only observe.
    """
    return "strict" if enforcement_mode in {"block", "redact"} else "guideline"


def policy_class_label(cls: str) -> str:
    return "Strictly Enforced" if cls == "strict" else "Guideline"


def policy_class_icon(cls: str) -> str:
    return "🛡️" if cls == "strict" else "📘"


# ─── Eligibility check ───────────────────────────────────────────────


def check_promotion_eligibility(
    instance: dict[str, Any],
    template: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check whether a Guideline instance is eligible to promote to Strict.

    Returns a dict with shape::

        {
            "eligible": bool,
            "days_in_guideline": int,
            "days_required": int,
            "false_positive_rate": float,  # 0.0-1.0
            "fp_rate_threshold": float,
            "approvers": [{"role": str, "status": "pending|approved|rejected", ...}],
            "blocking_reasons": list[str],
        }
    """
    now = now or datetime.now(UTC)
    blocking_reasons: list[str] = []

    # 1. Time in Guideline
    activated_at_str = instance.get("activated_at") or instance.get("activatedAt")
    activated_at = _parse_iso(activated_at_str) if activated_at_str else None
    days_in_guideline = int((now - activated_at).total_seconds() // 86_400) if activated_at else 0
    days_required = int(PROMOTION_DEFAULTS["days_in_guideline_required"])
    if days_in_guideline < days_required:
        blocking_reasons.append(
            f"Need {days_required - days_in_guideline} more day(s) in Guideline mode "
            f"(currently {days_in_guideline})"
        )

    # 2. False positive rate
    stats = instance.get("stats") or {}
    fp_rate = float(stats.get("false_positive_rate") or stats.get("falsePositiveRate") or 0)
    fp_threshold = float(PROMOTION_DEFAULTS["fp_rate_threshold"])
    if fp_rate > fp_threshold:
        blocking_reasons.append(
            f"False-positive rate is {fp_rate * 100:.1f}% — must be below {fp_threshold * 100:.1f}%"
        )

    # 3. Approvers — pull the latest promotion event's approver list,
    # synthesise pending entries for any required role that isn't
    # represented yet.
    required = required_approvers_for(template)
    history = instance.get("promotion_history") or instance.get("promotionHistory") or []
    existing_approvers: list[dict[str, Any]] = history[-1].get("approvers", []) if history else []
    approvers: list[dict[str, Any]] = []
    for role in required:
        found = next((a for a in existing_approvers if a.get("role") == role), None)
        approvers.append(found if found else {"role": role, "status": "pending"})

    missing = [a for a in approvers if a.get("status") != "approved"]
    if missing:
        blocking_reasons.append(f"Awaiting approval from: {', '.join(a['role'] for a in missing)}")

    return {
        "eligible": len(blocking_reasons) == 0,
        "days_in_guideline": days_in_guideline,
        "days_required": days_required,
        "false_positive_rate": fp_rate,
        "fp_rate_threshold": fp_threshold,
        "approvers": approvers,
        "blocking_reasons": blocking_reasons,
    }


# ─── Promotion / demotion actions ────────────────────────────────────


def promote_to_strict(
    *,
    instance: dict[str, Any],
    template: dict[str, Any],
    by: str,
    approvers: list[dict[str, Any]],
    rollout_strategy: str,  # "all" | "canary" | "phased"
    rollout_canary_app_id: str | None = None,
    rollout_percentage: int | None = None,
    target_mode: str | None = None,  # "block" or "redact"
    now: datetime | None = None,
) -> dict[str, Any]:
    """Promote a Guideline instance to Strict.

    Verifies eligibility (with the supplied approvers staged) and writes
    a promotion event into history. Caller persists the returned
    instance.

    Returns a dict::

        {"ok": True,  "instance": {...}}    on success
        {"ok": False, "reason": "..."}      on rejection
    """
    now = now or datetime.now(UTC)
    enforcement_mode = instance.get("enforcement_mode") or instance.get("enforcementMode", "log")

    if class_of(enforcement_mode) == "strict":
        return {"ok": False, "reason": "Instance is already in Strict mode"}

    # Stage the supplied approvers so eligibility check sees them
    history = list(instance.get("promotion_history") or instance.get("promotionHistory") or [])
    staged_event = {
        "from": "guideline",
        "to": "strict",
        "at": now.isoformat(),
        "by": by,
        "approvers": approvers,
    }
    merged = {**instance, "promotion_history": [*history, staged_event]}

    eligibility = check_promotion_eligibility(merged, template, now)
    if not eligibility["eligible"]:
        return {
            "ok": False,
            "reason": "Not eligible: " + "; ".join(eligibility["blocking_reasons"]),
        }

    # Decide target enforcement mode — explicit param wins, otherwise use
    # the template's suggested default if it's a strict mode, otherwise
    # fall back to "block".
    template_default = (template.get("defaults") or {}).get("enforcement_mode") or (
        template.get("defaults") or {}
    ).get("enforcementMode")
    if target_mode is not None:
        resolved_target = target_mode
    elif template_default in {"block", "redact"}:
        resolved_target = template_default
    else:
        resolved_target = "block"

    promotion_event = {
        "from": "guideline",
        "to": "strict",
        "at": now.isoformat(),
        "by": by,
        "approvers": approvers,
        "reason": f"Rollout strategy: {rollout_strategy}",
    }

    promoted = {
        **instance,
        "enforcement_mode": resolved_target,
        "rollout_strategy": rollout_strategy,
        "rollout_canary_app_id": rollout_canary_app_id,
        "rollout_percentage": rollout_percentage,
        "promotion_history": [*history, promotion_event],
        "updated_at": now.isoformat(),
    }
    return {"ok": True, "instance": promoted}


def demote_to_guideline(
    *,
    instance: dict[str, Any],
    by: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Demote a Strict instance back to Guideline.

    Always permitted — this is the safety valve. No approval required
    because *increasing* safety (less enforcement) doesn't need
    second-signature.
    """
    now = now or datetime.now(UTC)

    event = {
        "from": "strict",
        "to": "guideline",
        "at": now.isoformat(),
        "by": by,
        "reason": reason or "Manual demotion",
    }

    history = list(instance.get("promotion_history") or instance.get("promotionHistory") or [])
    return {
        **instance,
        "enforcement_mode": "log",  # Guideline default; admin can switch to "flag"
        "rollout_strategy": "all",
        "rollout_canary_app_id": None,
        "rollout_percentage": None,
        "promotion_history": [*history, event],
        "updated_at": now.isoformat(),
    }


# ─── Auto-demote watchdog ────────────────────────────────────────────


def assess_auto_demote(
    instance: dict[str, Any],
    config: dict[str, Any],
    first_detected_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate current stats against the auto-demote config.

    Caller is responsible for actually firing demote_to_guideline()
    after the grace period elapses.

    Typical wiring: a worker cron runs every minute, calls this for
    every Strict instance, persists "first detected" timestamps to track
    grace periods.

    Returns::

        {
            "should_demote": bool,
            "reason": str | None,
            "grace_until": str | None,   # ISO timestamp
        }
    """
    now = now or datetime.now(UTC)

    if not config.get("enabled", True):
        return {"should_demote": False, "reason": None, "grace_until": None}

    enforcement_mode = instance.get("enforcement_mode") or instance.get("enforcementMode", "log")
    if class_of(enforcement_mode) != "strict":
        return {"should_demote": False, "reason": None, "grace_until": None}

    stats = instance.get("stats") or {}
    fp_rate_pct = (
        float(stats.get("false_positive_rate") or stats.get("falsePositiveRate") or 0) * 100
    )
    threshold = float(config.get("fp_rate_threshold", AUTO_DEMOTE_DEFAULTS["fp_rate_threshold"]))

    if fp_rate_pct <= threshold:
        return {"should_demote": False, "reason": None, "grace_until": None}

    grace_seconds = int(config.get("grace_seconds", AUTO_DEMOTE_DEFAULTS["grace_seconds"]))

    # FP rate is over the cap. If this is the first detection, start grace.
    if first_detected_at is None:
        return {
            "should_demote": False,
            "reason": (f"FP rate {fp_rate_pct:.1f}% > {threshold}% — grace period started"),
            "grace_until": (now + timedelta(seconds=grace_seconds)).isoformat(),
        }

    grace_ends = first_detected_at + timedelta(seconds=grace_seconds)
    if now >= grace_ends:
        return {
            "should_demote": True,
            "reason": (
                f"FP rate {fp_rate_pct:.1f}% sustained above {threshold}% "
                f"for {grace_seconds}s — auto-demoting"
            ),
            "grace_until": None,
        }

    return {
        "should_demote": False,
        "reason": f"FP rate {fp_rate_pct:.1f}% — grace ends at {grace_ends.isoformat()}",
        "grace_until": grace_ends.isoformat(),
    }


# ─── Risk preview (for the promotion wizard) ─────────────────────────


def build_risk_preview(
    instance: dict[str, Any],
    detector_breakdown: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a risk preview from a Guideline instance's stats so admins
    can see the blast radius before promoting.

    Returns::

        {
            "total_evaluations": int,
            "would_block_count": int,
            "would_block_percentage": float,
            "estimated_false_positives": int,
            "estimated_affected_users": int,
            "top_reasons": [{"reason": str, "count": int, "pct": float}, ...],
        }
    """
    stats = instance.get("stats") or {}
    total_evaluations = int(
        stats.get("total_evaluations_30d") or stats.get("totalEvaluations30d") or 0
    )
    would_block_count = int(stats.get("total_hits_30d") or stats.get("totalHits30d") or 0)
    would_block_percentage = (
        (would_block_count / total_evaluations) * 100 if total_evaluations > 0 else 0.0
    )
    estimated_false_positives = int(
        stats.get("false_positives_30d") or stats.get("falsePositives30d") or 0
    )
    # Rough estimate: assume each FP is a unique user (worst case)
    estimated_affected_users = max(estimated_false_positives, int(would_block_count * 0.3))

    top_reasons: list[dict[str, Any]] = []
    if detector_breakdown:
        for reason, count in sorted(detector_breakdown.items(), key=lambda kv: kv[1], reverse=True)[
            :5
        ]:
            top_reasons.append(
                {
                    "reason": reason,
                    "count": count,
                    "pct": (count / would_block_count) * 100 if would_block_count > 0 else 0.0,
                }
            )

    return {
        "total_evaluations": total_evaluations,
        "would_block_count": would_block_count,
        "would_block_percentage": would_block_percentage,
        "estimated_false_positives": estimated_false_positives,
        "estimated_affected_users": estimated_affected_users,
        "top_reasons": top_reasons,
    }


# ─── Internal helpers ────────────────────────────────────────────────


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, normalising 'Z' suffix to +00:00.

    Always returns a timezone-aware datetime. Naive inputs are assumed
    UTC — same convention the dashboard uses.
    """
    s = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(s)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
