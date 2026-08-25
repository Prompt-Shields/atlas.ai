"""MCP server discovery — correlation, scoring, and inventory promotion (#241).

Pipeline: `get_collectors()` each emit raw `McpObservation`s → `correlate()`
groups them into `_Candidate`s by a normalized `(name, host)` key →
`score_server()` turns corroboration into a 0-100 confidence score →
`run_scan()` upserts one `DiscoveredMcpServer` row per candidate, tenant +
canonical-key scoped, idempotent across re-scans (see the collector
module's docstring). `promote()` copies a candidate into `AIAsset` — the
concrete "MCP server → AI inventory" hop the issue's acceptance criteria
asks for.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_asset import AIAsset
from app.models.mcp_discovery import DiscoveredMcpServer, McpDiscoveryStatus
from app.schemas.mcp_discovery import (
    DiscoveredMcpServerResponse,
    McpDiscoveryListResponse,
    McpDiscoveryScanResponse,
    McpServerPromoteResponse,
    McpServerStatusUpdate,
)
from app.services.mcp_discovery_collectors import McpObservation, get_collectors

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _normalize(value: str) -> str:
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-")


def canonical_key(name: str, host: str | None) -> str:
    """The correlation key: same normalized name+host = the same server."""
    return f"{_normalize(name)}@{_normalize(host) if host else ''}"


class _Candidate:
    """A correlated server, fused from every observation sharing its key."""

    def __init__(self, key: str, first: McpObservation) -> None:
        self.key = key
        self.name = first.name
        self.host = first.host
        self.transport = first.transport
        self.description = first.description
        self.sources: set[str] = {first.source}
        self.observation_count = 1

    def absorb(self, observation: McpObservation) -> None:
        self.sources.add(observation.source)
        self.observation_count += 1
        # Later, more-descriptive observations win the display fields —
        # network/browser sources tend to carry richer context than a bare
        # config-file reference.
        if observation.description:
            self.description = observation.description
        if observation.transport:
            self.transport = observation.transport


def correlate(observations: list[McpObservation]) -> list[_Candidate]:
    """Fuse raw observations into one candidate per distinct (name, host)."""
    candidates: dict[str, _Candidate] = {}
    for obs in observations:
        key = canonical_key(obs.name, obs.host)
        existing = candidates.get(key)
        if existing is None:
            candidates[key] = _Candidate(key, obs)
        else:
            existing.absorb(obs)
    return list(candidates.values())


def score_server(candidate: _Candidate) -> float:
    """Confidence score (0-100).

    Every corroborating source is worth 30 points, capped at 90 for 3+
    distinct sources; repeat observations from sources already counted add
    up to 10 more (2 points each, capped at 5 repeats). A single-source
    sighting tops out at 40 — real cross-source corroboration is what moves
    a candidate toward "safe to promote".
    """
    source_score = min(len(candidate.sources), 3) * 30
    repeat_observations = max(candidate.observation_count - len(candidate.sources), 0)
    repeat_score = min(repeat_observations, 5) * 2
    return float(min(source_score + repeat_score, 100))


def to_response(server: DiscoveredMcpServer) -> DiscoveredMcpServerResponse:
    return DiscoveredMcpServerResponse(
        id=str(server.id),
        canonical_key=server.canonical_key,
        name=server.name,
        host=server.host,
        transport=server.transport,
        description=server.description,
        sources=server.sources,
        observation_count=server.observation_count,
        confidence_score=server.confidence_score,
        status=server.status,
        promoted_asset_id=str(server.promoted_asset_id) if server.promoted_asset_id else None,
        last_observed_at=server.last_observed_at,
    )


async def list_servers(db: AsyncSession, tenant_id: uuid.UUID | None) -> McpDiscoveryListResponse:
    """All discovered MCP servers for the tenant, highest-confidence first."""
    query = select(DiscoveredMcpServer)
    if tenant_id is not None:
        query = query.where(DiscoveredMcpServer.tenant_id == tenant_id)
    result = await db.execute(query.order_by(DiscoveredMcpServer.confidence_score.desc()))
    return McpDiscoveryListResponse(servers=[to_response(s) for s in result.scalars().all()])


async def run_scan(db: AsyncSession, tenant_id: uuid.UUID) -> McpDiscoveryScanResponse:
    """Run every collector, correlate + score, and upsert by canonical key.

    Idempotent: a re-observed candidate updates its descriptive fields,
    sources, observation count, and score, but keeps its existing
    `status`/`promoted_asset_id` — a human's promote/dismiss survives
    re-scans, matching `agent_discovery_service.run_scan`.
    """
    observations: list[McpObservation] = []
    for collector in get_collectors():
        observations.extend(await collector.collect())

    candidates = correlate(observations)

    result = await db.execute(
        select(DiscoveredMcpServer).where(DiscoveredMcpServer.tenant_id == tenant_id)
    )
    existing = {s.canonical_key: s for s in result.scalars().all()}

    created = 0
    updated = 0
    touched: list[DiscoveredMcpServer] = []
    for candidate in candidates:
        server = existing.get(candidate.key)
        if server is None:
            server = DiscoveredMcpServer(
                tenant_id=tenant_id,
                canonical_key=candidate.key,
                status=McpDiscoveryStatus.candidate,
            )
            db.add(server)
            existing[candidate.key] = server
            created += 1
        else:
            updated += 1
        server.name = candidate.name
        server.host = candidate.host
        server.transport = candidate.transport
        server.description = candidate.description
        server.sources = sorted(candidate.sources)
        server.observation_count = candidate.observation_count
        server.confidence_score = score_server(candidate)
        server.last_observed_at = datetime.now(UTC)
        touched.append(server)

    await db.commit()
    for server in touched:
        await db.refresh(server)
    return McpDiscoveryScanResponse(
        scanned_observations=len(observations),
        created=created,
        updated=updated,
        servers=[to_response(s) for s in touched],
    )


async def update_status(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    server_id: uuid.UUID,
    payload: McpServerStatusUpdate,
) -> DiscoveredMcpServerResponse | None:
    """Dismiss (or reset) a discovered MCP server. None if not found."""
    query = select(DiscoveredMcpServer).where(DiscoveredMcpServer.id == server_id)
    if tenant_id is not None:
        query = query.where(DiscoveredMcpServer.tenant_id == tenant_id)
    result = await db.execute(query)
    server = result.scalar_one_or_none()
    if server is None:
        return None
    server.status = payload.status
    await db.commit()
    await db.refresh(server)
    return to_response(server)


async def promote(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
    server_id: uuid.UUID,
) -> McpServerPromoteResponse | None:
    """Create an `AIAsset` for a discovered MCP server and mark it promoted.

    None if the server doesn't exist (or belongs to another tenant).
    """
    result = await db.execute(
        select(DiscoveredMcpServer).where(
            DiscoveredMcpServer.id == server_id,
            DiscoveredMcpServer.tenant_id == tenant_id,
        )
    )
    server = result.scalar_one_or_none()
    if server is None:
        return None

    asset = AIAsset(
        tenant_id=tenant_id,
        org_id=org_id,
        name=server.name,
        description=server.description or f"MCP server discovered via {', '.join(server.sources)}.",
        deployment_status="shadow",
        model_type="mcp_server",
        is_third_party=True,
    )
    db.add(asset)
    await db.flush()

    server.status = McpDiscoveryStatus.promoted
    server.promoted_asset_id = asset.id
    await db.commit()
    await db.refresh(server)
    await db.refresh(asset)
    return McpServerPromoteResponse(server=to_response(server), asset_id=str(asset.id))
