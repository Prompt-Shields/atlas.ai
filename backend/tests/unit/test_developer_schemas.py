"""Unit tests for the Developer API schemas.

These exercise the Pydantic validation surface without touching the
database. Router-level integration tests live in tests/e2e/.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.developer import DeveloperScope, EventSeverity
from app.schemas.developer import (
    DeveloperAPIKeyCreate,
    DeveloperAPIKeyScopesUpdate,
    EventDefinitionCreate,
    EventIngestItem,
    EventIngestRequest,
)

pytestmark = pytest.mark.unit


class TestDeveloperAPIKeyCreate:
    def test_defaults_to_events_write(self) -> None:
        body = DeveloperAPIKeyCreate(name="ci-key")
        assert body.scopes == [DeveloperScope.EVENTS_WRITE]

    def test_accepts_multiple_scopes(self) -> None:
        body = DeveloperAPIKeyCreate(
            name="full",
            scopes=[
                DeveloperScope.EVENTS_WRITE,
                DeveloperScope.EVENTS_READ,
                DeveloperScope.EVENT_TYPES_WRITE,
            ],
        )
        assert len(body.scopes) == 3

    def test_rejects_empty_scopes(self) -> None:
        with pytest.raises(ValidationError):
            DeveloperAPIKeyCreate(name="empty", scopes=[])

    def test_name_required(self) -> None:
        with pytest.raises(ValidationError):
            DeveloperAPIKeyCreate(name="")  # type: ignore[arg-type]


class TestDeveloperAPIKeyScopesUpdate:
    def test_rejects_empty_scopes(self) -> None:
        with pytest.raises(ValidationError):
            DeveloperAPIKeyScopesUpdate(scopes=[])

    def test_accepts_subset(self) -> None:
        body = DeveloperAPIKeyScopesUpdate(scopes=[DeveloperScope.EVENTS_READ])
        assert body.scopes == [DeveloperScope.EVENTS_READ]


class TestEventDefinitionCreate:
    def test_valid_name_format(self) -> None:
        body = EventDefinitionCreate(name="agent.task_started")
        assert body.name == "agent.task_started"
        assert body.default_severity == EventSeverity.INFO

    def test_name_can_contain_dot_dash_underscore(self) -> None:
        body = EventDefinitionCreate(name="rag.retrieval-completed_v2")
        assert body.name == "rag.retrieval-completed_v2"

    def test_name_rejects_uppercase(self) -> None:
        with pytest.raises(ValidationError):
            EventDefinitionCreate(name="Agent.Task")

    def test_name_rejects_leading_digit(self) -> None:
        with pytest.raises(ValidationError):
            EventDefinitionCreate(name="2agent")

    def test_name_rejects_spaces(self) -> None:
        with pytest.raises(ValidationError):
            EventDefinitionCreate(name="agent task")

    def test_optional_schema_and_description(self) -> None:
        body = EventDefinitionCreate(
            name="ok",
            description="A thing",
            schema={"task_id": "string", "agent_id": "string"},
        )
        assert body.schema == {"task_id": "string", "agent_id": "string"}
        assert body.description == "A thing"


class TestEventIngestItem:
    def test_minimal_event(self) -> None:
        item = EventIngestItem(event_type="agent.task_started")
        assert item.payload == {}
        assert item.occurrences == 1
        assert item.severity is None  # filled from definition default

    def test_full_event(self) -> None:
        now = datetime.now(UTC)
        item = EventIngestItem(
            event_type="rag.retrieval_completed",
            severity=EventSeverity.WARNING,
            occurred_at=now,
            payload={"latency_ms": 230, "docs_returned": 4},
            source="rag-service",
            session_id="sess-001",
            correlation_id="corr-001",
            user_external_id="hr_user_42",
            occurrences=3,
        )
        assert item.payload["latency_ms"] == 230
        assert item.occurrences == 3

    def test_occurrences_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            EventIngestItem(event_type="x", occurrences=0)

    def test_occurrences_capped(self) -> None:
        with pytest.raises(ValidationError):
            EventIngestItem(event_type="x", occurrences=10_001)


class TestEventIngestRequest:
    def test_requires_at_least_one_event(self) -> None:
        with pytest.raises(ValidationError):
            EventIngestRequest(events=[])

    def test_caps_batch_at_500(self) -> None:
        events = [EventIngestItem(event_type="x") for _ in range(501)]
        with pytest.raises(ValidationError):
            EventIngestRequest(events=events)

    def test_accepts_typical_batch(self) -> None:
        events = [EventIngestItem(event_type="x") for _ in range(50)]
        body = EventIngestRequest(events=events)
        assert len(body.events) == 50
