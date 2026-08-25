"""Unit tests for app.services.sentinel_service — the seeded event generator."""

from __future__ import annotations

import pytest

from app.services.sentinel_service import (
    SentinelEventType,
    SentinelSeverity,
    generate_events,
)

pytestmark = [pytest.mark.unit]


class TestGenerateEvents:
    def test_no_enabled_types_returns_empty(self) -> None:
        assert generate_events(enabled_event_types=[]) == []

    def test_all_types_returns_every_seed(self) -> None:
        events = generate_events(enabled_event_types=list(SentinelEventType))
        assert len(events) == 8
        seen_types = {e["event_type"] for e in events}
        assert seen_types == set(SentinelEventType)

    def test_filters_to_requested_types_only(self) -> None:
        events = generate_events(enabled_event_types=[SentinelEventType.blocked])
        assert events
        assert all(e["event_type"] == SentinelEventType.blocked for e in events)

    def test_sorted_newest_first(self) -> None:
        events = generate_events(enabled_event_types=list(SentinelEventType))
        timestamps = [e["time_generated"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_never_includes_prompt_body_only_hash(self) -> None:
        events = generate_events(enabled_event_types=list(SentinelEventType))
        for e in events:
            assert "prompt" not in {k.lower() for k in e} or "prompt_hash" in e
            assert e["prompt_hash"]
            assert len(e["prompt_hash"]) == 16

    def test_severity_values_are_valid(self) -> None:
        events = generate_events(enabled_event_types=list(SentinelEventType))
        for e in events:
            assert e["severity"] in set(SentinelSeverity)
