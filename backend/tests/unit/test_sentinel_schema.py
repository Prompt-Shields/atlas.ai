"""Canonical PromptShieldsActivity_CL schema — validation and wire encoding.

The forwarder's whole schema-drift defence rests on `validate_row` catching a
bad row before it reaches Azure Monitor, so these tests are the guard on that
guard. They also pin the column list against docs/integrations/
microsoft-sentinel/data-schema.md, which declares this module canonical.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services import sentinel_schema as schema

pytestmark = [pytest.mark.unit]


def valid_row() -> dict:
    """A minimal row that passes validation — the base for mutation tests."""
    return {
        "TimeGenerated": datetime(2026, 5, 5, 9, 14, tzinfo=UTC),
        "EventId": "EV-1041",
        "TenantId": "ps-tenant-weinberg-01",
        "User": "l.park@example.org",
        "AiTool": "ChatGPT Business",
        "IsShadowAi": False,
        "EventType": "Redacted",
        "Severity": "Medium",
        "Detail": "Redacted SSN+EIN in ChatGPT Business",
        "PromptHash": "a3f1c",
    }


class TestColumnList:
    def test_matches_the_documented_column_set(self) -> None:
        # Transcribed from data-schema.md's canonical JSON block. If this list
        # changes, the doc and the customer's DCR must change with it.
        assert [c.name for c in schema.COLUMNS] == [
            "TimeGenerated",
            "EventId",
            "TenantId",
            "User",
            "UserAadObjectId",
            "Department",
            "AiTool",
            "IsShadowAi",
            "EventType",
            "SensitiveType",
            "Severity",
            "Detail",
            "PolicyId",
            "PolicyName",
            "EndpointId",
            "EndpointPlatform",
            "RedactionTokenCount",
            "PromptHash",
        ]

    def test_required_columns_match_the_doc(self) -> None:
        assert set(schema.REQUIRED_COLUMNS) == {
            "TimeGenerated",
            "EventId",
            "TenantId",
            "User",
            "AiTool",
            "IsShadowAi",
            "EventType",
            "Severity",
            "Detail",
            "PromptHash",
        }

    def test_stream_and_table_names_are_the_v1_contract(self) -> None:
        assert schema.STREAM_NAME == "Custom-PromptShields_v1"
        assert schema.TABLE_NAME == "PromptShieldsActivity_CL"


class TestValidateRow:
    def test_accepts_a_minimal_valid_row(self) -> None:
        assert schema.validate_row(valid_row()) == []

    def test_accepts_every_nullable_column_as_null(self) -> None:
        row = valid_row()
        for column in schema.COLUMNS:
            if not column.required:
                row[column.name] = None
        assert schema.validate_row(row) == []

    def test_rejects_an_unknown_column(self) -> None:
        # Sentinel drops unknown columns and can reject the batch, so this is
        # caught at source rather than poisoning the customer's table.
        row = valid_row() | {"PromptText": "the actual prompt"}
        reasons = schema.validate_row(row)
        assert any("PromptText" in r and "not a column" in r for r in reasons)

    def test_rejects_a_missing_required_column(self) -> None:
        row = valid_row()
        del row["PromptHash"]
        assert "PromptHash: missing required column" in schema.validate_row(row)

    def test_rejects_null_in_a_required_column(self) -> None:
        row = valid_row() | {"User": None}
        assert "User: null in a required column" in schema.validate_row(row)

    def test_rejects_empty_string_even_in_a_nullable_column(self) -> None:
        # data-schema.md: "do not send empty strings" — an empty string is
        # indistinguishable from a real value once it is in the table.
        row = valid_row() | {"Department": ""}
        reasons = schema.validate_row(row)
        assert any("Department" in r and "empty string" in r for r in reasons)

    def test_rejects_a_bool_where_an_int_belongs(self) -> None:
        # bool is a subclass of int in Python; the type check must not let
        # `True` through as a token count.
        row = valid_row() | {"RedactionTokenCount": True}
        assert "RedactionTokenCount: expected int" in schema.validate_row(row)

    def test_rejects_an_int_where_a_bool_belongs(self) -> None:
        row = valid_row() | {"IsShadowAi": 1}
        assert "IsShadowAi: expected boolean" in schema.validate_row(row)

    def test_rejects_a_non_string_in_a_string_column(self) -> None:
        row = valid_row() | {"EventId": 1041}
        assert "EventId: expected string" in schema.validate_row(row)

    def test_rejects_an_unparseable_datetime_string(self) -> None:
        row = valid_row() | {"TimeGenerated": "last Tuesday"}
        assert "TimeGenerated: not an ISO-8601 datetime" in schema.validate_row(row)

    def test_accepts_an_iso_datetime_string(self) -> None:
        row = valid_row() | {"TimeGenerated": "2026-05-05T09:14:00Z"}
        assert schema.validate_row(row) == []

    def test_reports_every_problem_not_just_the_first(self) -> None:
        # A dead-lettered batch should record the full story for the operator.
        row = valid_row() | {"User": None, "IsShadowAi": 1, "Unknown": "x"}
        assert len(schema.validate_row(row)) == 3


class TestSerialiseRow:
    def test_encodes_datetimes_as_iso_with_z(self) -> None:
        out = schema.serialise_row(valid_row())
        assert out["TimeGenerated"] == "2026-05-05T09:14:00Z"

    def test_treats_a_naive_datetime_as_utc(self) -> None:
        row = valid_row() | {"TimeGenerated": datetime(2026, 5, 5, 9, 14)}
        assert schema.serialise_row(row)["TimeGenerated"] == "2026-05-05T09:14:00Z"

    def test_converts_a_non_utc_datetime_to_utc(self) -> None:
        aware = datetime(2026, 5, 5, 9, 14, tzinfo=timezone(-timedelta(hours=5)))
        row = valid_row() | {"TimeGenerated": aware}
        assert schema.serialise_row(row)["TimeGenerated"] == "2026-05-05T14:14:00Z"

    def test_leaves_other_values_untouched(self) -> None:
        out = schema.serialise_row(valid_row())
        assert out["IsShadowAi"] is False
        assert out["EventId"] == "EV-1041"

    def test_output_still_validates(self) -> None:
        # Serialisation must not turn a valid row into an invalid one.
        assert schema.validate_row(schema.serialise_row(valid_row())) == []
