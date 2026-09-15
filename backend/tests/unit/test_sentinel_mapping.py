"""PromptEvent → PromptShieldsActivity_CL mapping.

Pure functions, no database: `build_row` takes its lookups as a
`MappingContext`. These tests pin the decisions that are easy to get subtly
wrong — which events are forwarded at all, how severity degrades, and the
privacy invariant that no prompt content can reach the wire.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import (
    PromptEventAction,
    PromptEventKind,
    PromptEventSeverity,
    PromptEventSource,
)
from app.services import sentinel_schema
from app.services.sentinel_mapping import (
    DeviceMatch,
    DirectoryMatch,
    MappingContext,
    UnmappableEvent,
    build_row,
    display_tool_name,
    event_type_for,
    is_shadow_ai,
    redaction_token_count_for,
    sensitive_type_for,
    severity_for,
)
from app.services.sentinel_service import SentinelEventType, SentinelSeverity

pytestmark = [pytest.mark.unit]

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
HASH = "a" * 64


def make_event(**overrides) -> PromptEvent:
    """A violation event that maps cleanly; override to explore the edges."""
    defaults: dict = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT_ID,
        "source": PromptEventSource.SAFARI_EXTENSION,
        "event_kind": PromptEventKind.VIOLATION,
        "app_id": "chatgpt.com",
        "prompt_hash": HASH,
        "action": PromptEventAction.REDACTED,
        "severity": PromptEventSeverity.MEDIUM,
        "pii_categories": {"ssn": 2, "ein": 1},
        "device_fingerprint": "fp-1",
        "user_external_id": "l.park@example.org",
        "vendor": "openai",
        "occurrences": 1,
        "occurred_at": datetime(2026, 5, 5, 9, 14, tzinfo=UTC),
    }
    defaults.update(overrides)
    return PromptEvent(**defaults)


class TestEventType:
    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            (PromptEventAction.REDACTED, SentinelEventType.redacted),
            (PromptEventAction.BLOCKED, SentinelEventType.blocked),
            (PromptEventAction.FLAGGED, SentinelEventType.coached),
        ],
    )
    def test_maps_the_action_vocabulary(self, action, expected) -> None:
        assert event_type_for(make_event(action=action)) == expected

    def test_flagged_with_a_protected_characteristic_becomes_bias_flagged(self) -> None:
        event = make_event(
            action=PromptEventAction.FLAGGED,
            pii_categories={"protected_characteristics": 1},
        )
        assert event_type_for(event) == SentinelEventType.bias_flagged

    def test_a_violation_with_no_action_is_a_coaching_moment(self) -> None:
        event = make_event(action=None, event_kind=PromptEventKind.VIOLATION)
        assert event_type_for(event) == SentinelEventType.coached

    @pytest.mark.parametrize("action", [PromptEventAction.ALLOWED, PromptEventAction.LOGGED, None])
    def test_ordinary_activity_is_not_forwarded(self, action) -> None:
        # Forwarding every allowed prompt would bury the SOC's signal and bill
        # the customer per GB for it.
        event = make_event(action=action, event_kind=PromptEventKind.ACTIVITY)
        with pytest.raises(UnmappableEvent):
            event_type_for(event)

    def test_anonymised_is_never_emitted(self) -> None:
        # `prompt_events` carries no signal separating anonymisation from
        # redaction, so emitting it would be a guess. Documented in the mapper.
        produced = {
            event_type_for(make_event(action=a))
            for a in (
                PromptEventAction.REDACTED,
                PromptEventAction.BLOCKED,
                PromptEventAction.FLAGGED,
            )
        }
        assert SentinelEventType.anonymised not in produced


class TestToolNaming:
    @pytest.mark.parametrize(
        ("app_id", "expected"),
        [
            ("chatgpt.com", "ChatGPT Business"),
            ("claude.ai", "Claude"),
            ("WWW.Perplexity.AI", "Perplexity"),
            ("copilot.microsoft.com", "Microsoft Copilot Premium"),
        ],
    )
    def test_canonical_display_names(self, app_id, expected) -> None:
        assert display_tool_name(make_event(app_id=app_id)) == expected

    def test_unknown_tool_keeps_its_own_name(self) -> None:
        # AiTool is free-form precisely so a new tool needs no schema bump.
        assert display_tool_name(make_event(app_id="mistral.ai")) == "mistral.ai"

    def test_falls_back_to_vendor_when_app_id_is_missing(self) -> None:
        assert display_tool_name(make_event(app_id=None, vendor="anthropic")) == "Claude"

    def test_unidentifiable_tool_is_named_unknown(self) -> None:
        assert display_tool_name(make_event(app_id=None, vendor=None)) == "Unknown"


class TestShadowAi:
    def test_unsanctioned_tool_is_shadow_ai(self) -> None:
        assert is_shadow_ai(make_event(), frozenset()) is True

    def test_tool_matching_an_active_use_case_is_not_shadow_ai(self) -> None:
        assert is_shadow_ai(make_event(), frozenset({"chatgpt.com"})) is False

    def test_use_case_registered_under_the_display_name_also_sanctions(self) -> None:
        # An admin registers "ChatGPT Business"; the extension reports
        # "chatgpt.com". Both must resolve to the same sanctioned tool.
        assert is_shadow_ai(make_event(), frozenset({"chatgpt business"})) is False

    def test_unidentifiable_tool_is_shadow_ai(self) -> None:
        event = make_event(app_id=None, vendor=None)
        assert is_shadow_ai(event, frozenset({"chatgpt.com"})) is True


class TestSensitiveType:
    def test_joins_categories_in_the_documented_shape(self) -> None:
        assert sensitive_type_for(make_event()) == "EIN+SSN"

    def test_unknown_category_is_humanised(self) -> None:
        event = make_event(pii_categories={"donor_list": 1})
        assert sensitive_type_for(event) == "Donor List"

    def test_no_categories_yields_null_not_empty_string(self) -> None:
        assert sensitive_type_for(make_event(pii_categories={})) is None

    def test_categories_collapsing_to_one_name_are_not_duplicated(self) -> None:
        event = make_event(pii_categories={"salary": 1, "compensation": 2})
        assert sensitive_type_for(event) == "Compensation"


class TestSeverity:
    @pytest.mark.parametrize(
        ("severity", "expected"),
        [
            (PromptEventSeverity.LOW, SentinelSeverity.low),
            (PromptEventSeverity.MEDIUM, SentinelSeverity.medium),
            (PromptEventSeverity.HIGH, SentinelSeverity.high),
            # Sentinel's enum has three values; `critical` must not be dropped.
            (PromptEventSeverity.CRITICAL, SentinelSeverity.high),
        ],
    )
    def test_maps_client_severity(self, severity, expected) -> None:
        event = make_event(severity=severity)
        assert severity_for(event, SentinelEventType.redacted) == expected

    def test_missing_severity_falls_back_to_the_event_type_default(self) -> None:
        event = make_event(severity=None)
        assert severity_for(event, SentinelEventType.blocked) == SentinelSeverity.high
        assert severity_for(event, SentinelEventType.coached) == SentinelSeverity.low


class TestRedactionTokenCount:
    def test_sums_categories_for_a_redaction(self) -> None:
        count = redaction_token_count_for(make_event(), SentinelEventType.redacted)
        assert count == 3

    def test_is_null_for_a_block(self) -> None:
        # The column is only meaningful for Redacted / Anonymised.
        assert redaction_token_count_for(make_event(), SentinelEventType.blocked) is None

    def test_zero_becomes_null_rather_than_a_misleading_zero(self) -> None:
        event = make_event(pii_categories={})
        assert redaction_token_count_for(event, SentinelEventType.redacted) is None


class TestBuildRow:
    def test_produces_a_row_that_passes_schema_validation(self) -> None:
        row = build_row(make_event(), tenant_id=TENANT_ID, context=MappingContext())
        assert sentinel_schema.validate_row(row) == []

    def test_denormalises_directory_and_device_facts(self) -> None:
        context = MappingContext(
            directory_by_user={
                "l.park@example.org": DirectoryMatch(
                    aad_object_id="f8b2c0e3-1a2d", department="Grants Management"
                )
            },
            devices_by_fingerprint={
                "fp-1": DeviceMatch(endpoint_id="device-9b3a", platform="macos")
            },
        )
        row = build_row(make_event(), tenant_id=TENANT_ID, context=context)
        assert row["UserAadObjectId"] == "f8b2c0e3-1a2d"
        assert row["Department"] == "Grants Management"
        assert row["EndpointId"] == "device-9b3a"
        assert row["EndpointPlatform"] == "macos"

    def test_missing_lookups_leave_nullable_columns_null(self) -> None:
        row = build_row(make_event(), tenant_id=TENANT_ID, context=MappingContext())
        assert row["UserAadObjectId"] is None
        assert row["Department"] is None
        assert row["EndpointId"] is None
        assert sentinel_schema.validate_row(row) == []

    def test_event_id_is_derived_from_the_row_uuid(self) -> None:
        event = make_event()
        row = build_row(event, tenant_id=TENANT_ID, context=MappingContext())
        # Stability matters: a replayed dead letter must carry the same id.
        assert row["EventId"] == f"EV-{event.id}"

    def test_unattributed_event_gets_a_device_scoped_user(self) -> None:
        event = make_event(user_external_id=None)
        row = build_row(event, tenant_id=TENANT_ID, context=MappingContext())
        assert row["User"] == "device:fp-1"
        assert sentinel_schema.validate_row(row) == []

    def test_fully_unattributed_event_still_validates(self) -> None:
        event = make_event(user_external_id=None, device_fingerprint=None)
        row = build_row(event, tenant_id=TENANT_ID, context=MappingContext())
        assert row["User"] == "unattributed"
        assert sentinel_schema.validate_row(row) == []

    def test_event_without_a_prompt_hash_is_unmappable(self) -> None:
        # PromptHash is required and the customer correlates on it; a
        # synthesised value would be worse than skipping the row.
        with pytest.raises(UnmappableEvent, match="prompt_hash"):
            build_row(make_event(prompt_hash=None), tenant_id=TENANT_ID, context=MappingContext())

    def test_detail_names_the_categories_not_any_content(self) -> None:
        row = build_row(make_event(), tenant_id=TENANT_ID, context=MappingContext())
        assert row["Detail"] == "Redacted EIN+SSN in ChatGPT Business"

    def test_detail_reports_rollup_occurrences(self) -> None:
        row = build_row(make_event(occurrences=7), tenant_id=TENANT_ID, context=MappingContext())
        assert "(7 occurrences)" in row["Detail"]

    def test_row_carries_only_documented_columns(self) -> None:
        # The privacy stance is structural: whatever a future PromptEvent field
        # holds, only these columns can ever reach Sentinel.
        row = build_row(make_event(), tenant_id=TENANT_ID, context=MappingContext())
        assert set(row) == {c.name for c in sentinel_schema.COLUMNS}

    def test_tenant_id_is_the_prompt_shields_tenant(self) -> None:
        row = build_row(make_event(), tenant_id=TENANT_ID, context=MappingContext())
        assert row["TenantId"] == str(TENANT_ID)
