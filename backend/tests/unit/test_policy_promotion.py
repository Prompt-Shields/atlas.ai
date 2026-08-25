"""Unit tests for app.services.policy_promotion.

Behaviour-equivalent port of `lib/policy-engine/promotion.ts`. Tests
lock down the eligibility gates, promotion + demotion state
transitions, auto-demote watchdog grace logic, risk-preview math, and
the per-category approver matrix.

Run with: ``pytest tests/unit/test_policy_promotion.py --noconftest``
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.policy_promotion import (
    AUTO_DEMOTE_DEFAULTS,
    PROMOTION_DEFAULTS,
    assess_auto_demote,
    build_risk_preview,
    check_promotion_eligibility,
    class_of,
    demote_to_guideline,
    policy_class_icon,
    policy_class_label,
    promote_to_strict,
    required_approvers_for,
)

# ─── Fixtures ────────────────────────────────────────────────────────

NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


def _instance(
    *,
    enforcement_mode: str = "log",
    activated_at: datetime | None = None,
    fp_rate: float = 0.0,
    promotion_history: list[dict] | None = None,
    stats_extra: dict | None = None,
) -> dict:
    """Build a PolicyInstance dict for tests."""
    stats: dict = {"false_positive_rate": fp_rate}
    if stats_extra:
        stats.update(stats_extra)
    return {
        "enforcement_mode": enforcement_mode,
        "activated_at": activated_at.isoformat() if activated_at else None,
        "stats": stats,
        "promotion_history": promotion_history or [],
    }


def _template(category: str = "OWASP_LLM", default_mode: str = "block") -> dict:
    return {
        "category": category,
        "defaults": {"enforcement_mode": default_mode},
    }


# ─── class_of / policy_class_label / policy_class_icon ───────────────


class TestClassOf:
    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("block", "strict"),
            ("redact", "strict"),
            ("flag", "guideline"),
            ("log", "guideline"),
            ("anything_else", "guideline"),
        ],
    )
    def test_mapping(self, mode, expected):
        assert class_of(mode) == expected

    def test_labels(self):
        assert policy_class_label("strict") == "Strictly Enforced"
        assert policy_class_label("guideline") == "Guideline"

    def test_icons(self):
        assert policy_class_icon("strict") == "🛡️"
        assert policy_class_icon("guideline") == "📘"


# ─── required_approvers_for ──────────────────────────────────────────


class TestRequiredApproversFor:
    @pytest.mark.parametrize(
        ("category", "expected"),
        [
            ("GDPR", ["DPO"]),
            ("EU_AI_ACT", ["DPO", "Security Lead"]),
            ("INDUSTRY", ["Security Lead", "Compliance Officer"]),
            ("OWASP_LLM", ["Security Lead"]),
            ("SHADOW_AI", ["Security Lead"]),
            ("CONTENT_SAFETY", ["Trust & Safety Lead"]),
            ("CUSTOM", ["Policy Author"]),
        ],
    )
    def test_known_categories(self, category, expected):
        assert required_approvers_for(_template(category=category)) == expected

    def test_unknown_category_falls_back_to_security_lead(self):
        assert required_approvers_for(_template(category="MADE_UP")) == ["Security Lead"]


# ─── check_promotion_eligibility ─────────────────────────────────────


class TestCheckPromotionEligibility:
    def test_fresh_instance_blocked_on_time(self):
        # Activated just now
        instance = _instance(activated_at=NOW)
        result = check_promotion_eligibility(instance, _template(), now=NOW)
        assert result["eligible"] is False
        assert result["days_in_guideline"] == 0
        assert any("more day" in r for r in result["blocking_reasons"])

    def test_no_activated_at_treated_as_zero_days(self):
        instance = _instance(activated_at=None)
        result = check_promotion_eligibility(instance, _template(), now=NOW)
        assert result["days_in_guideline"] == 0
        assert result["eligible"] is False

    def test_high_fp_rate_blocks(self):
        instance = _instance(
            activated_at=NOW - timedelta(days=20),
            fp_rate=0.05,  # 5% > 2% threshold
        )
        result = check_promotion_eligibility(instance, _template(), now=NOW)
        assert any("False-positive rate" in r for r in result["blocking_reasons"])

    def test_missing_approvers_block(self):
        instance = _instance(
            activated_at=NOW - timedelta(days=20),
            fp_rate=0.01,
        )
        # OWASP_LLM requires Security Lead
        result = check_promotion_eligibility(instance, _template(category="OWASP_LLM"), now=NOW)
        assert result["eligible"] is False
        assert any("Awaiting approval" in r for r in result["blocking_reasons"])
        assert result["approvers"][0]["role"] == "Security Lead"
        assert result["approvers"][0]["status"] == "pending"

    def test_eligible_when_all_gates_pass(self):
        history = [
            {
                "from": "guideline",
                "to": "strict",
                "at": NOW.isoformat(),
                "by": "admin",
                "approvers": [{"role": "Security Lead", "status": "approved"}],
            }
        ]
        instance = _instance(
            activated_at=NOW - timedelta(days=20),
            fp_rate=0.01,
            promotion_history=history,
        )
        result = check_promotion_eligibility(instance, _template(category="OWASP_LLM"), now=NOW)
        assert result["eligible"] is True
        assert result["blocking_reasons"] == []

    def test_camelcase_input_accepted(self):
        instance = {
            "enforcementMode": "log",
            "activatedAt": (NOW - timedelta(days=20)).isoformat(),
            "stats": {"falsePositiveRate": 0.01},
            "promotionHistory": [],
        }
        result = check_promotion_eligibility(instance, _template(), now=NOW)
        assert result["days_in_guideline"] == 20
        assert result["false_positive_rate"] == 0.01

    def test_iso_with_z_suffix_parses(self):
        instance = _instance()
        # Override activated_at directly with a Z-suffixed string (like what
        # JSON.stringify(new Date()) would produce in the dashboard)
        instance["activated_at"] = "2026-04-14T12:00:00Z"
        result = check_promotion_eligibility(instance, _template(), now=NOW)
        assert result["days_in_guideline"] == 20

    def test_thresholds_match_dashboard_defaults(self):
        # Lock down the exact defaults; cursor's M2.5 endpoints depend on these.
        assert PROMOTION_DEFAULTS["days_in_guideline_required"] == 14
        assert PROMOTION_DEFAULTS["fp_rate_threshold"] == 0.02


# ─── promote_to_strict ───────────────────────────────────────────────


class TestPromoteToStrict:
    def _eligible_instance(self) -> dict:
        return _instance(
            activated_at=NOW - timedelta(days=20),
            fp_rate=0.01,
        )

    def test_already_strict_rejected(self):
        instance = _instance(enforcement_mode="block")
        result = promote_to_strict(
            instance=instance,
            template=_template(),
            by="admin",
            approvers=[{"role": "Security Lead", "status": "approved"}],
            rollout_strategy="all",
            now=NOW,
        )
        assert result["ok"] is False
        assert "already in Strict" in result["reason"]

    def test_succeeds_with_approvers(self):
        result = promote_to_strict(
            instance=self._eligible_instance(),
            template=_template(category="OWASP_LLM"),
            by="admin",
            approvers=[{"role": "Security Lead", "status": "approved"}],
            rollout_strategy="canary",
            rollout_canary_app_id="app-1",
            now=NOW,
        )
        assert result["ok"] is True
        promoted = result["instance"]
        # Defaults to template's suggested mode
        assert promoted["enforcement_mode"] == "block"
        assert promoted["rollout_strategy"] == "canary"
        assert promoted["rollout_canary_app_id"] == "app-1"
        # New event appended
        assert len(promoted["promotion_history"]) == 1
        event = promoted["promotion_history"][0]
        assert event["from"] == "guideline"
        assert event["to"] == "strict"
        assert event["by"] == "admin"
        assert "canary" in event["reason"]

    def test_target_mode_param_overrides_template_default(self):
        result = promote_to_strict(
            instance=self._eligible_instance(),
            template=_template(category="OWASP_LLM", default_mode="block"),
            by="admin",
            approvers=[{"role": "Security Lead", "status": "approved"}],
            rollout_strategy="all",
            target_mode="redact",
            now=NOW,
        )
        assert result["ok"] is True
        assert result["instance"]["enforcement_mode"] == "redact"

    def test_template_with_log_default_falls_back_to_block(self):
        # Some templates suggest log/flag (e.g. EU AI Act audit-log policy)
        # When no explicit target, fall back to "block".
        result = promote_to_strict(
            instance=self._eligible_instance(),
            template=_template(category="OWASP_LLM", default_mode="log"),
            by="admin",
            approvers=[{"role": "Security Lead", "status": "approved"}],
            rollout_strategy="all",
            now=NOW,
        )
        assert result["instance"]["enforcement_mode"] == "block"

    def test_pending_approvers_blocks(self):
        result = promote_to_strict(
            instance=self._eligible_instance(),
            template=_template(category="EU_AI_ACT"),  # needs DPO + Security Lead
            by="admin",
            approvers=[{"role": "DPO", "status": "approved"}],  # missing Security Lead
            rollout_strategy="all",
            now=NOW,
        )
        assert result["ok"] is False
        assert "Security Lead" in result["reason"]


# ─── demote_to_guideline ─────────────────────────────────────────────


class TestDemoteToGuideline:
    def test_resets_mode_to_log_and_clears_rollout(self):
        instance = {
            "enforcement_mode": "block",
            "rollout_strategy": "phased",
            "rollout_canary_app_id": "app-1",
            "rollout_percentage": 50,
            "promotion_history": [],
        }
        result = demote_to_guideline(instance=instance, by="admin", now=NOW)
        assert result["enforcement_mode"] == "log"
        assert result["rollout_strategy"] == "all"
        assert result["rollout_canary_app_id"] is None
        assert result["rollout_percentage"] is None

    def test_appends_event_to_history(self):
        instance = {"enforcement_mode": "block", "promotion_history": []}
        result = demote_to_guideline(instance=instance, by="watchdog", reason="FP spike", now=NOW)
        assert len(result["promotion_history"]) == 1
        event = result["promotion_history"][0]
        assert event["from"] == "strict"
        assert event["to"] == "guideline"
        assert event["by"] == "watchdog"
        assert event["reason"] == "FP spike"

    def test_default_reason_is_manual_demotion(self):
        instance = {"enforcement_mode": "block", "promotion_history": []}
        result = demote_to_guideline(instance=instance, by="admin", now=NOW)
        assert result["promotion_history"][0]["reason"] == "Manual demotion"


# ─── assess_auto_demote ──────────────────────────────────────────────


class TestAssessAutoDemote:
    def _config(self, **overrides) -> dict:
        return {**AUTO_DEMOTE_DEFAULTS, **overrides}

    def test_disabled_config_never_demotes(self):
        instance = _instance(enforcement_mode="block", fp_rate=0.99)
        result = assess_auto_demote(instance, self._config(enabled=False), now=NOW)
        assert result["should_demote"] is False

    def test_guideline_instance_never_demotes(self):
        instance = _instance(enforcement_mode="log", fp_rate=0.99)
        result = assess_auto_demote(instance, self._config(), now=NOW)
        assert result["should_demote"] is False

    def test_below_threshold_clean(self):
        instance = _instance(enforcement_mode="block", fp_rate=0.01)  # 1%
        result = assess_auto_demote(instance, self._config(fp_rate_threshold=5), now=NOW)
        assert result["should_demote"] is False
        assert result["reason"] is None

    def test_first_detection_starts_grace(self):
        instance = _instance(enforcement_mode="block", fp_rate=0.10)  # 10%
        result = assess_auto_demote(
            instance,
            self._config(fp_rate_threshold=5, grace_seconds=300),
            first_detected_at=None,
            now=NOW,
        )
        assert result["should_demote"] is False
        assert "grace period started" in result["reason"]
        # Grace ends 300s from now
        expected_grace = (NOW + timedelta(seconds=300)).isoformat()
        assert result["grace_until"] == expected_grace

    def test_grace_in_progress_no_demote(self):
        instance = _instance(enforcement_mode="block", fp_rate=0.10)
        first_detected = NOW - timedelta(seconds=100)
        result = assess_auto_demote(
            instance,
            self._config(fp_rate_threshold=5, grace_seconds=300),
            first_detected_at=first_detected,
            now=NOW,
        )
        assert result["should_demote"] is False
        assert "grace ends at" in result["reason"]

    def test_grace_elapsed_demotes(self):
        instance = _instance(enforcement_mode="block", fp_rate=0.10)
        first_detected = NOW - timedelta(seconds=400)  # past 300s grace
        result = assess_auto_demote(
            instance,
            self._config(fp_rate_threshold=5, grace_seconds=300),
            first_detected_at=first_detected,
            now=NOW,
        )
        assert result["should_demote"] is True
        assert "auto-demoting" in result["reason"]


# ─── build_risk_preview ──────────────────────────────────────────────


class TestBuildRiskPreview:
    def test_zero_evaluations_returns_zero_pct(self):
        instance = _instance(
            stats_extra={
                "total_evaluations_30d": 0,
                "total_hits_30d": 0,
                "false_positives_30d": 0,
            }
        )
        result = build_risk_preview(instance)
        assert result["would_block_percentage"] == 0
        assert result["estimated_affected_users"] == 0

    def test_basic_math(self):
        instance = _instance(
            stats_extra={
                "total_evaluations_30d": 1000,
                "total_hits_30d": 100,
                "false_positives_30d": 5,
            }
        )
        result = build_risk_preview(instance)
        assert result["total_evaluations"] == 1000
        assert result["would_block_count"] == 100
        assert result["would_block_percentage"] == 10.0
        assert result["estimated_false_positives"] == 5
        # max(5, floor(100 * 0.3)) = max(5, 30) = 30
        assert result["estimated_affected_users"] == 30

    def test_top_reasons_sorted_descending_top_5(self):
        instance = _instance(stats_extra={"total_hits_30d": 100})
        breakdown = {"a": 5, "b": 50, "c": 10, "d": 20, "e": 8, "f": 3, "g": 4}
        result = build_risk_preview(instance, breakdown)
        reasons = [r["reason"] for r in result["top_reasons"]]
        assert reasons == ["b", "d", "c", "e", "a"]
        # Pct math: top one is 50/100 = 50%
        assert result["top_reasons"][0]["pct"] == 50.0

    def test_no_breakdown_empty_top_reasons(self):
        instance = _instance(stats_extra={"total_hits_30d": 100})
        result = build_risk_preview(instance)
        assert result["top_reasons"] == []

    def test_camelcase_stats_keys_accepted(self):
        instance = {
            "stats": {
                "totalEvaluations30d": 500,
                "totalHits30d": 50,
                "falsePositives30d": 2,
            }
        }
        result = build_risk_preview(instance)
        assert result["total_evaluations"] == 500
        assert result["would_block_count"] == 50


# ─── Constants pinning ───────────────────────────────────────────────


class TestConstants:
    """Lock the documented defaults — these are part of the wire
    contract with the dashboard's `policy-helpers.ts`."""

    def test_promotion_defaults(self):
        assert PROMOTION_DEFAULTS == {
            "days_in_guideline_required": 14,
            "fp_rate_threshold": 0.02,
        }

    def test_auto_demote_defaults(self):
        assert AUTO_DEMOTE_DEFAULTS == {
            "enabled": True,
            "fp_rate_threshold": 5,
            "window_minutes": 60,
            "grace_seconds": 300,
        }
