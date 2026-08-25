"""MCP server discovery collectors — one per observation source (#241).

Mirrors `agent_discovery_collectors.py`'s shape: each collector emits raw
`McpObservation`s for one intake channel; the service layer correlates and
scores them into `DiscoveredMcpServer` rows (observations themselves are not
persisted — only the fused candidate is). Sources are named after the
AISPM prototype's simulated intake channels — IDE config scanning, network
traffic sampling, browser extension telemetry, and host process listing.
Each is deterministic seed data; a future change would replace a source's
`SeedMcpCollector` with one that reads e.g. Claude Desktop's actual config
file, gated the same way `agent_discovery`'s `LiveAgentCollector` is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class McpObservation:
    """One piece of evidence that an MCP server exists, from one source."""

    source: str
    name: str
    host: str | None
    transport: str
    description: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class McpCollector(Protocol):
    """A source of `McpObservation`s for one intake channel."""

    source: str

    async def collect(self) -> list[McpObservation]: ...


class SeedMcpCollector:
    """Deterministic fixture data for one source (ported from AISPM's seed)."""

    def __init__(self, source: str, observations: list[McpObservation]) -> None:
        self.source = source
        self._observations = observations

    async def collect(self) -> list[McpObservation]:
        return list(self._observations)


# Two servers ("filesystem", "postgres-prod", "github-mcp") are deliberately
# corroborated by more than one source and one ("slack-mcp") by only one, so
# score_server() has real variation to differentiate.
_SEED_OBSERVATIONS: dict[str, list[McpObservation]] = {
    "ide_config": [
        McpObservation(
            source="ide_config",
            name="filesystem",
            host="localhost",
            transport="stdio",
            description="Local filesystem MCP server referenced in a Claude Desktop config.",
            evidence={
                "config_path": "~/Library/Application Support/Claude/claude_desktop_config.json"
            },
        ),
        McpObservation(
            source="ide_config",
            name="postgres-prod",
            host="db.internal.atlas.example",
            transport="stdio",
            description="Postgres MCP server referenced in a Cursor .cursor/mcp.json.",
            evidence={"config_path": ".cursor/mcp.json"},
        ),
    ],
    "network_traffic": [
        McpObservation(
            source="network_traffic",
            name="postgres-prod",
            host="db.internal.atlas.example",
            transport="http",
            description="Repeated MCP handshake traffic to an internal Postgres bridge.",
            evidence={"port": 8765, "requests_7d": 412},
        ),
        McpObservation(
            source="network_traffic",
            name="github-mcp",
            host="mcp.github.internal.atlas.example",
            transport="sse",
            description="SSE traffic matching the GitHub MCP server's event stream.",
            evidence={"port": 443, "requests_7d": 96},
        ),
    ],
    "browser_extension": [
        McpObservation(
            source="browser_extension",
            name="github-mcp",
            host="mcp.github.internal.atlas.example",
            transport="sse",
            description="Browser extension telemetry showing a Copilot-adjacent MCP connection.",
            evidence={"extension": "atlas-shadow-ai-sensor"},
        ),
    ],
    "process_list": [
        McpObservation(
            source="process_list",
            name="filesystem",
            host="localhost",
            transport="stdio",
            description="A running mcp-server-filesystem process observed on an enrolled endpoint.",
            evidence={"pid_sample": 4821, "devices_observed": 3},
        ),
        McpObservation(
            source="process_list",
            name="slack-mcp",
            host="localhost",
            transport="stdio",
            description="A running mcp-server-slack process observed on an enrolled endpoint.",
            evidence={"pid_sample": 5590, "devices_observed": 1},
        ),
    ],
}


def get_collectors() -> list[McpCollector]:
    """The collectors a scan should run — one per intake channel."""
    return [SeedMcpCollector(source, obs) for source, obs in _SEED_OBSERVATIONS.items()]
