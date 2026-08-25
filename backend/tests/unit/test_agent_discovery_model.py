"""#233 · Agent discovery model — metadata + registration (DB-free)."""

from app.models.agent_discovery import (
    AgentCloudProvider,
    AgentDiscoveryStatus,
    DiscoveredAgent,
)


def test_table_and_schema():
    assert DiscoveredAgent.__tablename__ == "discovered_agents"
    assert DiscoveredAgent.__table__.schema == "grc"
    cols = {c.name for c in DiscoveredAgent.__table__.columns}
    assert cols >= {
        "tenant_id",
        "provider",
        "external_id",
        "name",
        "registry",
        "runtime",
        "region",
        "description",
        "status",
        "owner_user_id",
        "raw_metadata",
        "last_seen_at",
    }


def test_upsert_key_constraint():
    constraint_names = {c.name for c in DiscoveredAgent.__table__.constraints}
    assert any(n.startswith("uq") and "provider_external_id" in n for n in constraint_names)


def test_enum_values_match_migration():
    assert AgentCloudProvider.aws_bedrock_agentcore.value == "aws_bedrock_agentcore"
    assert AgentCloudProvider.azure_ai_foundry.value == "azure_ai_foundry"
    assert AgentCloudProvider.gcp_knowledge_catalog.value == "gcp_knowledge_catalog"
    assert AgentDiscoveryStatus.shadow.value == "shadow"
    assert AgentDiscoveryStatus.pending.value == "pending"
    assert AgentDiscoveryStatus.approved.value == "approved"
    assert AgentDiscoveryStatus.dismissed.value == "dismissed"


def test_default_status_is_shadow():
    col = DiscoveredAgent.__table__.columns["status"]
    assert col.default.arg == AgentDiscoveryStatus.shadow


def test_registered_on_app_models():
    from app import models

    assert models.DiscoveredAgent is DiscoveredAgent
