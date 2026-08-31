"""Shared KQL validation helpers for the shipped Sentinel content.

Both `infra/sentinel-analytic-rules.json` and `infra/sentinel-workbook.json`
are deployed verbatim into a customer's Log Analytics workspace, and Azure
validates nothing about whether their KQL matches the table the forwarder
actually writes. A query naming a column we do not emit deploys cleanly and
then returns nothing forever — an analytic rule that never fires, or a
workbook tile that is permanently empty. Either way the customer believes
they have coverage they do not have.

These helpers let the tests for both artifacts hold every query against
`app.services.sentinel_schema`, the same module the forwarder validates
outbound rows with, so the two can never drift apart silently.
"""

from __future__ import annotations

import re
from datetime import timedelta

from app.services import sentinel_schema

# Anything PascalCase in a query must be a table column, a name the query
# binds itself, or the table name. The allow-list is deliberately empty: a
# typo'd column should surface as a failure, not be waved through.
KQL_ALLOWED_TOKENS: set[str] = set()

_STRING_LITERAL_RE = re.compile(r'"[^"]*"')
_IDENTIFIER_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*\b")
# `let X =` and `summarize X = ...` both bind a name. `==`, `>=` and `!=` are
# excluded: the negative lookahead rejects `==`, and the other operators put a
# non-word character immediately before the `=`.
_ALIAS_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
# Workbook parameter references — `{TimeRange}`, `{Tool:value}` and friends.
_PARAM_REF_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}")
_EVENT_TYPE_RE = re.compile(r'EventType\s*==\s*"([^"]*)"')
_SEVERITY_RE = re.compile(r'\bSeverity\s*==\s*"([^"]*)"')
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_duration(value: str) -> timedelta:
    """Parse the ISO-8601 duration subset Sentinel uses (`PT1H`, `P14D`)."""
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(f"not an ISO-8601 duration this parser handles: {value!r}")
    days, hours, minutes = (int(g) if g else 0 for g in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes)


def strip_strings(query: str) -> str:
    """Blank out string literals so their contents are not read as identifiers."""
    return _STRING_LITERAL_RE.sub('""', query)


def strip_params(query: str) -> str:
    """Blank out `{Parameter}` references, which are substituted before Azure
    ever parses the KQL and so are not column references."""
    return _PARAM_REF_RE.sub("", query)


def aliases_in(query: str) -> set[str]:
    """Names the query binds itself — `let` variables and summarize outputs."""
    return set(_ALIAS_RE.findall(strip_params(strip_strings(query))))


def param_refs_in(query: str) -> set[str]:
    """Workbook parameter names the query substitutes into."""
    return set(_PARAM_REF_RE.findall(query))


def schema_columns() -> set[str]:
    return {c.name for c in sentinel_schema.COLUMNS}


def unknown_identifiers(query: str, *, extra_known: set[str] | None = None) -> list[str]:
    """PascalCase tokens that are neither columns, the table, nor query-bound.

    This is the check the whole arrangement exists for. Empty means every
    column the query touches is one the forwarder really emits.
    """
    known = schema_columns()
    known.add(sentinel_schema.TABLE_NAME)
    known |= aliases_in(query)
    known |= KQL_ALLOWED_TOKENS
    known |= extra_known or set()

    found = set(_IDENTIFIER_RE.findall(strip_params(strip_strings(query))))
    return sorted(found - known)


def event_type_literals(query: str) -> list[str]:
    """Values the query compares `EventType` against."""
    return _EVENT_TYPE_RE.findall(query)


def severity_literals(query: str) -> list[str]:
    """Values the query compares `Severity` against."""
    return _SEVERITY_RE.findall(query)
