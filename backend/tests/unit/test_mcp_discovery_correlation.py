"""#241 · MCP discovery correlation + scoring — pure functions (DB-free)."""

from __future__ import annotations

import pytest

from app.services.mcp_discovery_collectors import McpObservation, get_collectors
from app.services.mcp_discovery_service import canonical_key, correlate, score_server

pytestmark = pytest.mark.unit


async def test_seed_collectors_are_deterministic():
    first = [o for c in get_collectors() for o in await c.collect()]
    second = [o for c in get_collectors() for o in await c.collect()]
    assert first == second
    assert len(first) > 0


def test_canonical_key_normalizes_case_and_punctuation():
    assert canonical_key("Postgres Prod", "DB.Internal.Atlas.Example") == canonical_key(
        "postgres-prod", "db.internal.atlas.example"
    )


def test_canonical_key_handles_missing_host():
    assert canonical_key("filesystem", None) == "filesystem@"


async def test_correlate_fuses_multi_source_observations():
    observations = [o for c in get_collectors() for o in await c.collect()]
    candidates = {c.key: c for c in correlate(observations)}

    # "filesystem" is observed by ide_config + process_list.
    fs = candidates[canonical_key("filesystem", "localhost")]
    assert fs.sources == {"ide_config", "process_list"}
    assert fs.observation_count == 2

    # "slack-mcp" is observed by only process_list.
    slack = candidates[canonical_key("slack-mcp", "localhost")]
    assert slack.sources == {"process_list"}
    assert slack.observation_count == 1


def test_correlate_is_order_independent():
    a = McpObservation(source="a", name="thing", host="h", transport="stdio")
    b = McpObservation(source="b", name="Thing", host="H", transport="http")
    assert {c.key for c in correlate([a, b])} == {c.key for c in correlate([b, a])}


def test_score_server_rewards_source_diversity_over_repeats():
    single_source_repeated = correlate(
        [McpObservation(source="a", name="x", host="h", transport="stdio") for _ in range(5)]
    )[0]
    two_sources = correlate(
        [
            McpObservation(source="a", name="y", host="h", transport="stdio"),
            McpObservation(source="b", name="y", host="h", transport="stdio"),
        ]
    )[0]
    assert score_server(two_sources) > score_server(single_source_repeated)


def test_score_server_caps_at_100_and_floors_at_0():
    # 3 distinct sources (90 pts) + 5 repeat observations from those same
    # sources (10 pts) = 100, the maximum.
    maxed_out = correlate(
        [McpObservation(source=s, name="z", host="h", transport="stdio") for s in ("a", "b", "c")]
        + [McpObservation(source="a", name="z", host="h", transport="stdio") for _ in range(5)]
    )[0]
    assert score_server(maxed_out) == 100.0

    single = correlate([McpObservation(source="a", name="w", host="h", transport="stdio")])[0]
    assert 0.0 < score_server(single) <= 100.0
