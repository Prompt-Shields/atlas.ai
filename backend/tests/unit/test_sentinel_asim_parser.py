"""The shipped ASIM AuditEvent parser must conform to ASIM and to our table.

`infra/sentinel-asim-parser.json` is deployed verbatim into a customer's
workspace and has two contracts to keep, both of which fail silently:

  * **Our table.** A column reference the forwarder does not emit makes the
    parser return nothing, or return rows with a null where a mandatory ASIM
    field should be. Same failure mode as the rules and workbook.

  * **ASIM itself.** This one is worse, because it fails while appearing to
    work. A parser emitting `EventType = 'PromptBlocked'` — not an ASIM value —
    or omitting a mandatory field still deploys, still returns rows, and still
    unions into `imAuditEvent`. The customer's existing ASIM detections then
    quietly skip or mis-bucket our events, and nothing anywhere errors. The
    entire reason an enterprise asks for a parser is that their queries should
    not have to know about us; a non-conformant parser breaks precisely that
    promise while looking installed and healthy.

So the ASIM vocabularies below are transcribed from the schema reference and
asserted against, rather than trusted to have been typed correctly once.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.services import sentinel_schema
from app.services.sentinel_service import SentinelEventType, SentinelSeverity
from tests.sentinel_kql import schema_columns

pytestmark = [pytest.mark.unit]

INFRA = pathlib.Path(__file__).resolve().parents[3] / "infra"
PARSER_PATH = INFRA / "sentinel-asim-parser.json"
BICEP_PATH = INFRA / "sentinel-asim-parser.bicep"


@pytest.fixture(scope="module")
def parser() -> dict:
    return json.loads(PARSER_PATH.read_text())


@pytest.fixture(scope="module")
def query(parser: dict) -> str:
    return parser["query"]


# ─── ASIM contract, transcribed from the schema reference ────────────
#
# ASIM Audit Event schema, EventSchemaVersion 0.1.2.

ASIM_MANDATORY_FIELDS = (
    "EventCount",
    "EventStartTime",
    "EventEndTime",
    "EventType",
    "EventResult",
    "EventProduct",
    "EventVendor",
    "EventSchema",
    "EventSchemaVersion",
    "Dvc",
    "Operation",
    "Object",
)

# The closed vocabularies. A value outside these is not ASIM, however sensible
# it reads.
ASIM_EVENT_TYPES = {
    "Set",
    "Read",
    "Create",
    "Delete",
    "Execute",
    "Install",
    "Clear",
    "Enable",
    "Disable",
    "Initialize",
    "Start",
    "Stop",
    "Other",
}
ASIM_EVENT_RESULTS = {"Success", "Partial", "Failure", "NA"}
ASIM_EVENT_SEVERITIES = {"Informational", "Low", "Medium", "High"}

# The filtering parameters an AuditEvent parser must accept, per the schema
# reference. The unifying parser calls with exactly these.
ASIM_FILTER_PARAMS = (
    "starttime",
    "endtime",
    "srcipaddr_has_any_prefix",
    "actorusername_has_any",
    "operation_has_any",
    "eventtype_in",
    "eventresult",
    "object_has_any",
    "newvalue_has_any",
)


class TestAsimConformance:
    def test_every_mandatory_field_is_produced(self, query: str) -> None:
        """A missing mandatory field is a null column in the customer's query.

        Not an error anywhere — the parser deploys and returns rows, and the
        analyst sees a blank where ASIM promises a value.
        """
        # Must check for an actual assignment, not mere presence of the name.
        # A substring check passes for `Object` while only `ObjectType` is
        # assigned — which is exactly how a dropped mandatory field slipped
        # past an earlier version of this test.
        missing = [f for f in ASIM_MANDATORY_FIELDS if not re.search(rf"\b{f}\b\s*=(?!=)", query)]
        assert not missing, f"parser does not assign mandatory ASIM fields: {missing}"

    def test_declares_the_audit_event_schema(self, parser: dict, query: str) -> None:
        assert parser["eventSchema"] == "AuditEvent"
        assert "EventSchema = 'AuditEvent'" in query

    def test_schema_version_is_consistent_between_metadata_and_query(
        self, parser: dict, query: str
    ) -> None:
        """Two places state the version; disagreeing is how they rot apart."""
        declared = parser["eventSchemaVersion"]
        assert f"EventSchemaVersion = '{declared}'" in query

    def test_event_type_values_are_all_in_the_asim_vocabulary(self, query: str) -> None:
        """The check that catches an intuitive but non-ASIM value.

        `EventType = 'Blocked'` reads perfectly and is wrong: ASIM's EventType
        is a closed set, and a value outside it lands our rows in no bucket any
        existing detection queries.
        """
        emitted = set(re.findall(r"EventType\s*=\s*'([^']*)'", query))
        invalid = emitted - ASIM_EVENT_TYPES
        assert not invalid, f"non-ASIM EventType values: {sorted(invalid)}"
        assert emitted, "parser never assigns EventType"

    def test_event_result_values_are_all_in_the_asim_vocabulary(self, query: str) -> None:
        # Values reach EventResult through the lookup table, so check the
        # table's right-hand column as well as any direct assignment.
        emitted = set(re.findall(r"EventResult\s*=\s*'([^']*)'", query))
        lookup_values = set(
            re.findall(r"'(?:Blocked|Redacted|Anonymised|Coached|BiasFlagged)',\s*'([^']*)'", query)
        )
        invalid = (emitted | lookup_values) - ASIM_EVENT_RESULTS
        assert not invalid, f"non-ASIM EventResult values: {sorted(invalid)}"
        assert lookup_values, "parser never maps our event types onto EventResult"

    def test_event_severity_values_are_all_in_the_asim_vocabulary(self, query: str) -> None:
        """ASIM severity is Informational/Low/Medium/High.

        Ours happens to overlap on three, which is exactly why this is worth
        asserting: the mapping looks like a no-op and would not be noticed if
        someone passed a value straight through that ASIM does not accept.
        """
        emitted = set(re.findall(r"EventSeverity\w*\s*=\s*'([^']*)'", query))
        lookup_values = set(re.findall(r"'(?:High|Medium|Low)',\s*'([^']*)'", query))
        invalid = (emitted | lookup_values) - ASIM_EVENT_SEVERITIES
        assert not invalid, f"non-ASIM EventSeverity values: {sorted(invalid)}"

    def test_accepts_every_required_filtering_parameter(self, parser: dict) -> None:
        """Signature must match what the unifying parser calls with.

        A parser missing one of these cannot be added to a unifying parser at
        all — the call fails on arity.
        """
        signature = parser["functionParameters"]
        missing = [p for p in ASIM_FILTER_PARAMS if f"{p}:" not in signature]
        assert not missing, f"parser signature omits ASIM filter params: {missing}"

    def test_accepts_the_disabled_parameter(self, parser: dict, query: str) -> None:
        """Source-specific parsers take `disabled` so they can be switched off.

        The unifying parser passes it from the ASimDisabledParsers watchlist;
        a parser that accepts it but ignores it cannot be turned off.
        """
        assert "disabled:bool" in parser["functionParameters"]
        assert "where not(disabled)" in query

    def test_every_filter_parameter_is_actually_used(self, parser: dict, query: str) -> None:
        """Accepting a filter and ignoring it is worse than rejecting it.

        The caller believes they narrowed the result set. For the two filters
        this source cannot honour (source IP and NewValue, neither of which we
        record) the parser returns nothing rather than returning everything —
        so this asserts each parameter appears in the body, not merely in the
        signature.
        """
        body = query.split("let parser", 1)[1]
        unused = [p for p in ASIM_FILTER_PARAMS if p not in body]
        assert not unused, f"filter params accepted but never applied: {unused}"


class TestOurTableContract:
    def test_reads_the_table_the_forwarder_writes(self, query: str) -> None:
        assert sentinel_schema.TABLE_NAME in query

    def test_every_source_column_referenced_is_one_we_emit(self, query: str) -> None:
        """The rules-and-workbook check, applied to the parser.

        Only source columns are checked: the parser deliberately introduces
        many ASIM names that are not in our table, which is its whole job.
        """
        known = schema_columns()
        referenced = set(re.findall(r"column_ifexists\('([^']+)'", query))
        unknown = referenced - known
        assert not unknown, f"parser reads columns the forwarder never emits: {sorted(unknown)}"

    def test_optional_columns_are_read_defensively(self, query: str) -> None:
        """Nullable columns go through column_ifexists.

        A custom table only materialises a column once a row has carried it, so
        referencing a never-yet-populated optional column directly fails the
        whole query at runtime — taking the conformant rows down with it.
        """
        optional = {c.name for c in sentinel_schema.COLUMNS if not c.required}
        guarded = set(re.findall(r"column_ifexists\('([^']+)'", query))
        # Only the optional columns the parser actually uses need guarding.
        used_unguarded = {
            name
            for name in optional
            if re.search(rf"(?<!')\b{name}\b(?!')", query) and name not in guarded
        }
        assert not used_unguarded, (
            f"optional columns referenced without column_ifexists: {sorted(used_unguarded)}"
        )

    def test_maps_every_event_type_the_forwarder_can_emit(self, query: str) -> None:
        """A forwarder event type with no mapping becomes EventResult 'NA'.

        Silently, and only for that type — so a new event type would look fine
        in aggregate while one slice of events lost its result.
        """
        mapped = set(re.findall(r"'(Blocked|Redacted|Anonymised|Coached|BiasFlagged)',", query))
        expected = {e.value for e in SentinelEventType}
        assert expected - mapped == set(), f"event types with no ASIM mapping: {expected - mapped}"

    def test_maps_every_severity_the_forwarder_can_emit(self, query: str) -> None:
        mapped = set(re.findall(r"'(High|Medium|Low)',\s*'", query))
        expected = {s.value for s in SentinelSeverity}
        assert expected - mapped == set(), f"severities with no ASIM mapping: {expected - mapped}"

    def test_operation_carries_our_enforcement_decision(self, query: str) -> None:
        """Operation is where an analyst finds "which prompts were blocked".

        ASIM's EventType is 'Other' for all of our events by design, so if
        Operation did not carry the decision it would be unreachable from ASIM.
        """
        assert re.search(r"Operation\s*=\s*EventType_PS", query)


class TestBicepContract:
    """The template and the JSON must not drift."""

    @pytest.fixture(scope="class")
    def bicep(self) -> str:
        return BICEP_PATH.read_text()

    def test_bicep_loads_the_json_rather_than_restating_it(self, bicep: str) -> None:
        assert "loadJsonContent('./sentinel-asim-parser.json')" in bicep

    def test_every_field_the_bicep_reads_exists_in_the_json(self, bicep: str, parser: dict) -> None:
        """Derived from the template source, standing in for the compiler.

        There is no bicep CLI in this environment, so a `parser.typo` would
        otherwise only surface at deployment time.
        """
        # Strip comments and string literals first: the filename in
        # loadJsonContent('./sentinel-asim-parser.json') otherwise reads as a
        # `parser.json` field reference and fails this on a false positive.
        code = re.sub(r"//[^\n]*", "", bicep)
        code = re.sub(r"'[^']*'", "''", code)
        referenced = set(re.findall(r"\bparser\.([A-Za-z][A-Za-z0-9_]*)", code))
        missing = referenced - set(parser)
        assert not missing, f"bicep reads fields absent from the JSON: {sorted(missing)}"

    def test_deploys_as_an_asim_categorised_saved_search(self, bicep: str, parser: dict) -> None:
        """Category 'ASIM' is what makes the portal treat it as a parser."""
        assert parser["category"] == "ASIM"
        assert "savedSearches@" in bicep

    def test_function_alias_follows_the_asim_naming_convention(self, parser: dict) -> None:
        """vim<Schema><Vendor><Product> for a source-specific filtering parser.

        The name is the integration point: a customer's unifying parser calls
        it by this alias.
        """
        alias = parser["functionAlias"]
        assert alias.startswith("vimAuditEvent"), alias
        assert alias == "vimAuditEventPromptShields"

    def test_resource_name_is_deterministic(self, bicep: str) -> None:
        """A redeploy must update in place, not stack a second parser."""
        assert "name: parser.functionAlias" in bicep
