"""PEP models — metadata + schema (DB-free, issue #237)."""

from app.models.policy_enforcement import (
    EnforcementMode,
    PolicyCategory,
    PolicyInstance,
    PolicyInstanceStatus,
    PolicySeverity,
    PolicyTemplate,
    RolloutStrategy,
)


def test_tables_and_schema():
    assert PolicyTemplate.__tablename__ == "policy_templates"
    assert PolicyInstance.__tablename__ == "policy_instances"
    for m in (PolicyTemplate, PolicyInstance):
        assert m.__table__.schema == "grc"


def test_template_is_tenant_agnostic():
    """The catalog carries no tenant_id — it is shared, read-only reference data."""
    cols = {c.name for c in PolicyTemplate.__table__.columns}
    assert "tenant_id" not in cols
    assert cols >= {
        "slug",
        "name",
        "version",
        "category",
        "author",
        "severity",
        "description",
        "triggers",
        "detectors",
        "actions",
        "tunable_parameters",
        "default_enforcement_mode",
        "default_applies_to",
        "tags",
    }
    constraint_names = {c.name for c in PolicyTemplate.__table__.constraints}
    assert any(n.startswith("uq") and "slug" in n for n in constraint_names)


def test_instance_is_tenant_scoped():
    cols = {c.name for c in PolicyInstance.__table__.columns}
    assert "tenant_id" in cols
    assert cols >= {
        "name",
        "template_id",
        "template_version",
        "parameter_values",
        "enforcement_mode",
        "severity",
        "applies_to",
        "status",
        "promotion_history",
        "auto_demote",
        "rollout_strategy",
        "stats",
    }
    fk_targets = {
        fk.target_fullname for col in PolicyInstance.__table__.columns for fk in col.foreign_keys
    }
    assert "grc.policy_templates.id" in fk_targets


def test_enum_values_match_migration():
    assert PolicyCategory.owasp_llm.value == "OWASP_LLM"
    assert PolicyCategory.eu_ai_act.value == "EU_AI_ACT"
    assert PolicyCategory.gdpr.value == "GDPR"
    assert PolicyCategory.industry.value == "INDUSTRY"
    assert PolicyCategory.shadow_ai.value == "SHADOW_AI"
    assert PolicyCategory.content_safety.value == "CONTENT_SAFETY"
    assert PolicyCategory.custom.value == "CUSTOM"

    assert {m.value for m in EnforcementMode} == {"log", "flag", "block", "redact"}
    assert {m.value for m in PolicySeverity} == {"low", "medium", "high", "critical"}
    assert {m.value for m in PolicyInstanceStatus} == {
        "draft",
        "testing",
        "active",
        "paused",
        "archived",
    }
    assert {m.value for m in RolloutStrategy} == {"all", "canary", "phased"}


def test_models_exported():
    from app import models

    assert models.PolicyTemplate is PolicyTemplate
    assert models.PolicyInstance is PolicyInstance
