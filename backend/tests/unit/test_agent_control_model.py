"""#235 · Agent control state model — metadata + registration (DB-free)."""

from app.models.agent_control import (
    AgentControlAction,
    AgentControlState,
    AgentLifecycleOverride,
)


def test_table_and_schema():
    assert AgentControlState.__tablename__ == "agent_control_states"
    assert AgentControlState.__table__.schema == "grc"
    cols = {c.name for c in AgentControlState.__table__.columns}
    assert cols >= {
        "tenant_id",
        "agent_id",
        "lifecycle_override",
        "guardrail_enabled",
        "last_action",
        "last_action_at",
        "last_action_by",
    }


def test_unique_constraint_on_tenant_and_agent():
    constraint_names = {c.name for c in AgentControlState.__table__.constraints}
    assert "uq_agent_control_state_agent" in constraint_names


def test_enum_values_match_migration():
    assert AgentLifecycleOverride.none.value == "none"
    assert AgentLifecycleOverride.paused.value == "paused"
    assert AgentLifecycleOverride.quarantined.value == "quarantined"
    assert AgentControlAction.pause.value == "pause"
    assert AgentControlAction.quarantine.value == "quarantine"
    assert AgentControlAction.toggle_guardrail.value == "toggle-guardrail"


def test_defaults():
    cols = AgentControlState.__table__.columns
    assert cols["lifecycle_override"].default.arg == AgentLifecycleOverride.none
    assert cols["guardrail_enabled"].default.arg is True


def test_registered_on_app_models():
    from app import models

    assert models.AgentControlState is AgentControlState
