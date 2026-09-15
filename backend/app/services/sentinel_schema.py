"""Canonical ``PromptShieldsActivity_CL`` column schema.

Mirrors ``docs/integrations/microsoft-sentinel/data-schema.md``, which names
this column list the machine-readable source that both the forwarder's
outbound payload and the customer Bicep template derive from. Keep the three
in lockstep: a column added here must exist in the customer's DCR before the
forwarder emits it, or Azure Monitor silently drops it.

Validation is deliberately strict. Spec §6 (schema drift) requires the
forwarder to "pre-validate against the latest DCR schema and reject-at-source
rather than poisoning the customer's table" — so a row that fails validation
goes to the dead-letter queue with a reason and never reaches the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Declared in the customer's DCR. The ``_v1`` suffix lets the schema evolve
# without breaking customer KQL — see "Schema versioning" in data-schema.md.
STREAM_NAME = "Custom-PromptShields_v1"
TABLE_NAME = "PromptShieldsActivity_CL"


@dataclass(frozen=True)
class Column:
    """One column of the custom table.

    ``type`` uses the data-schema.md vocabulary (``datetime``/``string``/
    ``boolean``/``int``) rather than Python types, so this list stays a
    faithful transcription of the doc and of the Bicep table definition.
    """

    name: str
    type: str
    required: bool


COLUMNS: tuple[Column, ...] = (
    Column("TimeGenerated", "datetime", True),
    Column("EventId", "string", True),
    Column("TenantId", "string", True),
    Column("User", "string", True),
    Column("UserAadObjectId", "string", False),
    Column("Department", "string", False),
    Column("AiTool", "string", True),
    Column("IsShadowAi", "boolean", True),
    Column("EventType", "string", True),
    Column("SensitiveType", "string", False),
    Column("Severity", "string", True),
    Column("Detail", "string", True),
    Column("PolicyId", "string", False),
    Column("PolicyName", "string", False),
    Column("EndpointId", "string", False),
    Column("EndpointPlatform", "string", False),
    Column("RedactionTokenCount", "int", False),
    Column("PromptHash", "string", True),
)

COLUMN_BY_NAME: dict[str, Column] = {c.name: c for c in COLUMNS}
REQUIRED_COLUMNS: tuple[str, ...] = tuple(c.name for c in COLUMNS if c.required)


def _type_error(column: Column, value: Any) -> str | None:
    """Return a reason string when ``value`` does not fit ``column.type``."""
    if column.type == "boolean":
        # bool is a subclass of int, so this check must precede "int".
        return None if isinstance(value, bool) else f"{column.name}: expected boolean"
    if column.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{column.name}: expected int"
        return None
    if column.type == "string":
        if not isinstance(value, str):
            return f"{column.name}: expected string"
        # data-schema.md: "null is allowed wherever marked nullable; do not
        # send empty strings." An empty string in Sentinel is indistinguishable
        # from a real value, so it would quietly corrupt the customer's data.
        if not value:
            return f"{column.name}: empty string (send null instead)"
        return None
    if column.type == "datetime":
        if isinstance(value, datetime):
            return None
        if isinstance(value, str) and value:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return f"{column.name}: not an ISO-8601 datetime"
            return None
        return f"{column.name}: expected datetime"
    return f"{column.name}: unknown column type {column.type!r}"


def validate_row(row: Mapping[str, Any]) -> list[str]:
    """Return every reason ``row`` is unfit for the wire; empty means valid.

    All reasons are collected rather than short-circuiting, so a dead-lettered
    batch records the full story instead of only the first problem.
    """
    reasons: list[str] = []

    for key in row:
        if key not in COLUMN_BY_NAME:
            # Sentinel rejects unknown columns by default; sending one poisons
            # the whole batch, so this is caught before it leaves the process.
            reasons.append(f"{key}: not a column of {TABLE_NAME}")

    for column in COLUMNS:
        if column.name not in row:
            if column.required:
                reasons.append(f"{column.name}: missing required column")
            continue
        value = row[column.name]
        if value is None:
            if column.required:
                reasons.append(f"{column.name}: null in a required column")
            continue
        error = _type_error(column, value)
        if error:
            reasons.append(error)

    return reasons


def serialise_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a mapped row to its JSON wire form.

    Datetimes become ISO-8601 with a ``Z`` suffix (Azure Monitor parses these
    back to ``datetime`` on ingest); naive datetimes are read as UTC, which is
    what every producer in this codebase stores. Keys absent from the row are
    left absent rather than emitted as null — the DCR treats both identically
    and the shorter payload buys headroom against the 1 MB request cap.
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            moment = value if value.tzinfo else value.replace(tzinfo=UTC)
            out[key] = moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
        else:
            out[key] = value
    return out
