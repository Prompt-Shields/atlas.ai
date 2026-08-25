"""#241 · MCP discovery model — metadata + registration (DB-free)."""

from app.models.mcp_discovery import DiscoveredMcpServer, McpDiscoveryStatus


def test_table_and_schema():
    assert DiscoveredMcpServer.__tablename__ == "discovered_mcp_servers"
    assert DiscoveredMcpServer.__table__.schema == "grc"
    cols = {c.name for c in DiscoveredMcpServer.__table__.columns}
    assert cols >= {
        "tenant_id",
        "canonical_key",
        "name",
        "host",
        "transport",
        "description",
        "sources",
        "observation_count",
        "confidence_score",
        "status",
        "promoted_asset_id",
        "last_observed_at",
    }


def test_upsert_key_constraint():
    constraint_names = {c.name for c in DiscoveredMcpServer.__table__.constraints}
    assert any(n.startswith("uq") and "canonical_key" in n for n in constraint_names)


def test_enum_values_match_migration():
    assert McpDiscoveryStatus.candidate.value == "candidate"
    assert McpDiscoveryStatus.promoted.value == "promoted"
    assert McpDiscoveryStatus.dismissed.value == "dismissed"


def test_default_status_is_candidate():
    col = DiscoveredMcpServer.__table__.columns["status"]
    assert col.default.arg == McpDiscoveryStatus.candidate


def test_registered_on_app_models():
    from app import models

    assert models.DiscoveredMcpServer is DiscoveredMcpServer
