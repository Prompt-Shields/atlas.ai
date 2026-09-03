"""Sentinel workbook validated against the schema the forwarder emits.

`infra/sentinel-workbook.json` is deployed verbatim into a customer's Sentinel
gallery by `infra/sentinel-workbook.bicep`. A tile whose KQL names a column the
forwarder does not emit renders as a permanently empty chart — no error in the
portal, nothing in a log, and a customer who concludes they have no shadow AI
because the shadow-AI tile is blank. That silent-empty failure is what these
tests exist to prevent.

Same arrangement as `test_sentinel_analytic_rules.py`, sharing its helpers via
`tests.sentinel_kql`: every query is held against `app.services.sentinel_schema`,
the module the forwarder validates outbound rows with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services import sentinel_schema
from app.services.sentinel_service import SentinelEventType, SentinelSeverity
from tests.sentinel_kql import (
    event_type_literals,
    param_refs_in,
    severity_literals,
    unknown_identifiers,
)

pytestmark = [pytest.mark.unit]

_INFRA = Path(__file__).resolve().parents[3] / "infra"
WORKBOOK_PATH = _INFRA / "sentinel-workbook.json"
BICEP_PATH = _INFRA / "sentinel-workbook.bicep"

# Workbook item types used here (Application Insights workbook schema):
TEXT_ITEM = 1
QUERY_ITEM = 3
PARAMETER_ITEM = 9


def load_workbook() -> dict[str, Any]:
    return json.loads(WORKBOOK_PATH.read_text())


def query_items() -> list[dict[str, Any]]:
    return [i for i in load_workbook()["items"] if i["type"] == QUERY_ITEM]


def declared_parameters() -> set[str]:
    names: set[str] = set()
    for item in load_workbook()["items"]:
        if item["type"] == PARAMETER_ITEM:
            for param in item["content"]["parameters"]:
                names.add(param["name"])
    return names


def parameter_queries() -> list[tuple[str, str]]:
    """`(parameter name, query)` for parameters that populate from a query."""
    out: list[tuple[str, str]] = []
    for item in load_workbook()["items"]:
        if item["type"] == PARAMETER_ITEM:
            for param in item["content"]["parameters"]:
                if param.get("query"):
                    out.append((param["name"], param["query"]))
    return out


def query_names() -> list[str]:
    return [i["name"] for i in query_items()]


@pytest.fixture(params=query_names())
def tile(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Each query tile as its own parametrised case, named by its item name."""
    return next(i for i in query_items() if i["name"] == request.param)


class TestFileShape:
    def test_workbook_sits_where_the_bicep_expects_it(self) -> None:
        # loadJsonContent('./sentinel-workbook.json') resolves relative to the
        # .bicep, so both must live in infra/.
        assert WORKBOOK_PATH.is_file()
        assert BICEP_PATH.is_file()
        assert "sentinel-workbook.json" in BICEP_PATH.read_text()

    def test_is_a_recognised_workbook_document(self) -> None:
        workbook = load_workbook()
        assert workbook["version"] == "Notebook/1.0"
        assert "Application-Insights-Workbooks" in workbook["$schema"]

    def test_has_tiles_to_show(self) -> None:
        assert len(query_items()) >= 5

    def test_item_names_are_unique(self) -> None:
        names = [i["name"] for i in load_workbook()["items"]]
        assert len(names) == len(set(names))

    def test_every_item_is_a_type_the_portal_renders(self) -> None:
        allowed = {TEXT_ITEM, QUERY_ITEM, PARAMETER_ITEM}
        for item in load_workbook()["items"]:
            assert item["type"] in allowed, f"{item['name']} has type {item['type']}"


class TestTilesReferenceRealColumns:
    def test_every_tile_targets_the_canonical_table(self, tile) -> None:
        assert sentinel_schema.TABLE_NAME in tile["content"]["query"]

    def test_every_identifier_is_a_column_or_bound_by_the_query(self, tile) -> None:
        """The core check: no tile may query a column we do not emit."""
        unknown = unknown_identifiers(tile["content"]["query"])
        assert not unknown, (
            f"tile {tile['name']} references {unknown}, which are neither columns "
            f"of {sentinel_schema.TABLE_NAME} nor bound by the query. A tile like "
            "this renders empty forever, with no error."
        )

    def test_parameter_dropdown_queries_also_use_real_columns(self) -> None:
        # A parameter query that returns nothing yields an empty dropdown, which
        # then filters every tile down to nothing.
        for name, query in parameter_queries():
            unknown = unknown_identifiers(query)
            assert not unknown, f"parameter {name} references {unknown}"

    def test_tiles_declare_a_title(self, tile) -> None:
        # An untitled chart is unreadable in a gallery of eight.
        assert tile["content"].get("title")

    def test_tiles_declare_a_visualization(self, tile) -> None:
        assert tile["content"].get("visualization")


class TestParameters:
    def test_every_referenced_parameter_is_declared(self, tile) -> None:
        # `{TimeRange}` with no matching parameter is a portal error on load.
        declared = declared_parameters()
        used = param_refs_in(tile["content"]["query"])
        missing = sorted(used - declared)
        assert not missing, f"tile {tile['name']} uses undeclared parameters {missing}"

    def test_the_time_range_parameter_exists_and_is_wired_up(self) -> None:
        assert "TimeRange" in declared_parameters()
        # Without timeContextFromParameter the picker changes nothing.
        for item in query_items():
            assert item["content"].get("timeContextFromParameter") == "TimeRange", (
                f"tile {item['name']} ignores the time-range picker"
            )

    def test_tool_filter_is_applied_to_every_tile(self) -> None:
        # A filter that silently skips some tiles is worse than no filter: the
        # page would show a mix of filtered and unfiltered numbers.
        for item in query_items():
            assert "AiToolFilter" in item["content"]["query"], (
                f"tile {item['name']} ignores the AI tool filter"
            )


class TestQueriesUseRealEnumValues:
    def test_event_type_comparisons_use_real_wire_values(self, tile) -> None:
        valid = {e.value for e in SentinelEventType}
        for value in event_type_literals(tile["content"]["query"]):
            assert value in valid, (
                f"tile {tile['name']} compares EventType to {value!r}; the "
                f"forwarder only ever emits {sorted(valid)}"
            )

    def test_severity_comparisons_use_real_wire_values(self, tile) -> None:
        valid = {s.value for s in SentinelSeverity}
        for value in severity_literals(tile["content"]["query"]):
            assert value in valid, (
                f"tile {tile['name']} compares Severity to {value!r}; the "
                f"forwarder only ever emits {sorted(valid)}"
            )

    def test_no_tile_keys_on_an_event_type_the_mapper_never_emits(self) -> None:
        # `Anonymised` is in the wire contract but unreachable from
        # prompt_events (see sentinel_mapping.event_type_for), so a tile keyed
        # on it would be permanently empty by construction.
        emittable = {
            SentinelEventType.redacted.value,
            SentinelEventType.blocked.value,
            SentinelEventType.coached.value,
            SentinelEventType.bias_flagged.value,
        }
        for item in query_items():
            for value in event_type_literals(item["content"]["query"]):
                assert value in emittable, (
                    f"tile {item['name']} keys on EventType {value!r}, which the mapper never emits"
                )


class TestGovernance:
    def test_no_tile_asks_for_prompt_content(self) -> None:
        """The product stance is structural — assert it rather than trust it."""
        banned = ("PromptText", "PromptBody", "Prompt_Body", "RawPrompt", "Content")
        for item in query_items():
            for token in banned:
                assert token not in item["content"]["query"], (
                    f"tile {item['name']} references {token}"
                )

    def test_the_privacy_stance_is_stated_on_the_page(self) -> None:
        # Someone reading this dashboard should learn, without asking, that the
        # hash is a correlation key and not recoverable prompt text.
        markdown = " ".join(
            i["content"]["json"] for i in load_workbook()["items"] if i["type"] == TEXT_ITEM
        )
        assert "PromptHash" in markdown
        assert "no prompt content" in markdown.lower()
