"""The shipped data connector tile must match the table the forwarder writes.

`infra/sentinel-data-connector.json` is deployed verbatim into a customer's
workspace, and like the rules, workbook and parser before it, Azure validates
nothing about whether its KQL matches reality. The failure modes here are
quiet in a particular way, because the tile is the customer's *only* at-a-glance
answer to "is this integration working?":

  * A `connectivityCriteria` query naming a column we do not emit leaves the
    tile permanently **grey while data flows in perfectly**. The customer
    concludes the integration is broken and opens a ticket about a system that
    is working.
  * A `graphQueries.baseQuery` with a bad filter draws a flat-zero ingestion
    chart next to a connected tile — the two disagree and neither errors.
  * A `sampleQueries` entry that does not run is the first thing an analyst
    clicks. It is the tile's credibility.

So every query in the tile is held against `app.services.sentinel_schema`, the
same module the forwarder validates outbound rows with.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.services import sentinel_schema
from app.services.sentinel_service import SentinelEventType, SentinelSeverity
from tests.sentinel_kql import (
    event_type_literals,
    severity_literals,
    unknown_identifiers,
)

pytestmark = [pytest.mark.unit]

INFRA = pathlib.Path(__file__).resolve().parents[3] / "infra"
CONNECTOR_PATH = INFRA / "sentinel-data-connector.json"
BICEP_PATH = INFRA / "sentinel-data-connector.bicep"

# Names the tile's queries may use beyond our own columns: the ASIM parser
# function this integration also ships, and aliases the queries bind.
EXTRA_KNOWN = {
    "vimAuditEventPromptShields",
    "Operation",
    "EventResult",
    "IsConnected",
    "LastLogReceived",
    "Time",
    "Events",
    "People",
    "Tools",
}


@pytest.fixture(scope="module")
def connector() -> dict:
    return json.loads(CONNECTOR_PATH.read_text())


def all_queries(connector: dict) -> list[tuple[str, str]]:
    """Every KQL string in the tile, labelled by where it came from."""
    found: list[tuple[str, str]] = []
    for i, value in enumerate(connector["connectivityCriteria"]["value"]):
        found.append((f"connectivityCriteria[{i}]", value))
    for dt in connector["dataTypes"]:
        found.append((f"dataTypes[{dt['name']}]", dt["lastDataReceivedQuery"]))
    for gq in connector["graphQueries"]:
        found.append((f"graphQueries[{gq['metricName']}]", gq["baseQuery"]))
    for sq in connector["sampleQueries"]:
        found.append((f"sampleQueries[{sq['description']}]", sq["query"]))
    return found


class TestQueriesMatchOurTable:
    def test_every_query_references_only_columns_we_emit(self, connector: dict) -> None:
        """The check the whole file exists for."""
        problems: list[str] = []
        for label, query in all_queries(connector):
            unknown = unknown_identifiers(query, extra_known=EXTRA_KNOWN)
            if unknown:
                problems.append(f"{label}: {unknown}")
        assert not problems, f"queries reference unknown identifiers: {problems}"

    def test_every_query_targets_the_forwarder_table_or_the_parser(self, connector: dict) -> None:
        """A query against some other table would silently measure nothing."""
        allowed = {sentinel_schema.TABLE_NAME, "vimAuditEventPromptShields"}
        for label, query in all_queries(connector):
            assert any(t in query for t in allowed), f"{label} targets no known source"

    def test_event_type_literals_are_ones_the_mapper_emits(self, connector: dict) -> None:
        valid = {e.value for e in SentinelEventType}
        for label, query in all_queries(connector):
            for literal in event_type_literals(query):
                assert literal in valid, (
                    f"{label} filters on EventType {literal!r}, which is never emitted"
                )

    def test_severity_literals_are_ones_the_mapper_emits(self, connector: dict) -> None:
        valid = {s.value for s in SentinelSeverity}
        for label, query in all_queries(connector):
            for literal in severity_literals(query):
                assert literal in valid, (
                    f"{label} filters on Severity {literal!r}, which is never emitted"
                )


class TestConnectivitySignal:
    """The tile's green dot is the customer's only cheap health check."""

    def test_connectivity_uses_the_query_form_not_the_polling_form(self, connector: dict) -> None:
        """`HasDataConnectors` is for polling connectors and would never go green.

        It reports connected when an active *poller* connection exists. We have
        none by design — nothing polls — so the tile would sit grey forever
        while data arrived.
        """
        assert connector["connectivityCriteria"]["type"] == "isConnectedQuery"

    def test_connectivity_query_yields_a_boolean_named_is_connected(self, connector: dict) -> None:
        """The contract Sentinel reads. A differently-named column is ignored."""
        query = connector["connectivityCriteria"]["value"][0]
        assert "IsConnected" in query

    def test_connectivity_query_is_time_bounded(self, connector: dict) -> None:
        """Without a window the tile reads connected forever after one event.

        A customer whose forwarding broke six months ago would still see green,
        which is worse than no tile at all.
        """
        query = connector["connectivityCriteria"]["value"][0]
        assert re.search(r"ago\(\d+[dhm]\)", query), "connectivity query has no freshness window"

    def test_data_type_names_the_real_table(self, connector: dict) -> None:
        names = {dt["name"] for dt in connector["dataTypes"]}
        assert sentinel_schema.TABLE_NAME in names


class TestTileContract:
    def test_kind_is_static_not_customizable(self, connector: dict) -> None:
        """`Customizable` is the CCP/API-polling kind; we push.

        Declaring it would advertise a polling connector that does not exist.
        """
        assert connector["kind"] == "Static"

    def test_graph_queries_table_name_matches_the_table(self, connector: dict) -> None:
        assert connector["graphQueriesTableName"] == sentinel_schema.TABLE_NAME

    def test_has_instructions_for_a_human_to_follow(self, connector: dict) -> None:
        """The tile's job is to tell an admin what to do; empty steps fail that."""
        steps = connector["instructionSteps"]
        assert len(steps) >= 3, "connect flow needs the app reg, the template, and our UI"
        for step in steps:
            assert step.get("title"), f"instruction step without a title: {step}"

    def test_describes_the_prompt_content_stance(self, connector: dict) -> None:
        """The single most important thing a security reviewer reads here.

        They are being asked to let a third party write into their SIEM; that we
        never ship prompt bodies belongs on the tile, not only in a runbook they
        may never open.
        """
        blurb = connector["descriptionMarkdown"]
        assert "PromptHash" in blurb
        assert re.search(r"never", blurb, re.IGNORECASE)

    def test_availability_is_marked_generally_available(self, connector: dict) -> None:
        # 1 = Available. 3 would render the tile as "Coming soon" and refuse to
        # let the customer connect something that in fact works.
        assert connector["availability"]["status"] == 1
        assert connector["availability"]["isPreview"] is False


class TestBicepContract:
    @pytest.fixture(scope="class")
    def bicep(self) -> str:
        return BICEP_PATH.read_text()

    def test_bicep_loads_the_json_rather_than_restating_it(self, bicep: str) -> None:
        assert "loadJsonContent('./sentinel-data-connector.json')" in bicep

    def test_every_field_the_bicep_reads_exists_in_the_json(
        self, bicep: str, connector: dict
    ) -> None:
        """Derived from the template source, standing in for the compiler.

        Comments and string literals are stripped first so a filename does not
        read as a field reference.
        """
        code = re.sub(r"//[^\n]*", "", bicep)
        code = re.sub(r"'[^']*'", "''", code)
        referenced = set(re.findall(r"\bconnector\.([A-Za-z][A-Za-z0-9_]*)", code))
        missing = referenced - set(connector)
        assert not missing, f"bicep reads fields absent from the JSON: {sorted(missing)}"

    def test_every_required_ui_config_field_is_passed_through(
        self, bicep: str, connector: dict
    ) -> None:
        """Omitting a required connectorUiConfig field fails at deploy time.

        There is no bicep CLI here, so this stands in for that error.
        """
        required = (
            "connectivityCriteria",
            "dataTypes",
            "graphQueries",
            "sampleQueries",
            "permissions",
            "instructionSteps",
        )
        missing = [f for f in required if f"{f}: connector.{f}" not in bicep]
        assert not missing, f"connectorUiConfig omits required fields: {missing}"

    def test_resource_name_is_deterministic(self, bicep: str) -> None:
        assert "name: connector.connectorId" in bicep
