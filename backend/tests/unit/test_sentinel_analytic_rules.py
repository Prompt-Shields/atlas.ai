"""Sentinel analytic rules validated against the schema the forwarder emits.

`infra/sentinel-analytic-rules.json` is deployed verbatim into a customer's
workspace by `infra/sentinel-analytic-rules.bicep`. Nothing in Azure checks
that a rule's KQL matches the table we actually write: a rule referencing a
column the forwarder does not emit deploys cleanly, returns nothing forever,
and leaves the SOC believing it is covered. That silent-no-op is the failure
this file exists to prevent.

So these tests parse every rule query and hold it against
`app.services.sentinel_schema` — the same module the forwarder validates
outbound rows with — plus the `SentinelEventType` / `SentinelSeverity` wire
enums. Rename a column in the schema without updating the rules and this goes
red.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services import sentinel_schema
from app.services.sentinel_service import SentinelEventType, SentinelSeverity

pytestmark = [pytest.mark.unit]

_INFRA = Path(__file__).resolve().parents[3] / "infra"
RULES_PATH = _INFRA / "sentinel-analytic-rules.json"
BICEP_PATH = _INFRA / "sentinel-analytic-rules.bicep"

# `rule.displayName`, `rule.query`, … as the template dereferences them.
_BICEP_FIELD_RE = re.compile(r"\brule\.([A-Za-z][A-Za-z0-9_]*)")

# Severities Sentinel accepts on a scheduled rule. Distinct from the wire
# `Severity` column, which carries only Low/Medium/High.
SENTINEL_RULE_SEVERITIES = {"Informational", "Low", "Medium", "High"}

# Sentinel caps a scheduled rule's queryPeriod at 14 days.
MAX_QUERY_PERIOD = timedelta(days=14)

# PascalCase tokens that are legal in a query without being table columns.
# Deliberately tiny: anything else must be a column or a query-local alias, so
# a typo'd column name surfaces as a failure rather than passing silently.
_KQL_ALLOWED_TOKENS: set[str] = set()

_STRING_LITERAL_RE = re.compile(r'"[^"]*"')
_IDENTIFIER_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*\b")
# `let X =` and `summarize X = ...` both bind a name. `==`, `>=`, `!=` are
# excluded: the negative lookahead rejects `==`, and the other operators put a
# non-word character immediately before the `=`.
_ALIAS_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
_EVENT_TYPE_RE = re.compile(r'EventType\s*==\s*"([^"]*)"')
_SEVERITY_RE = re.compile(r'\bSeverity\s*==\s*"([^"]*)"')
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def load_rules() -> list[dict[str, Any]]:
    payload = json.loads(RULES_PATH.read_text())
    return payload["rules"]


def rule_ids() -> list[str]:
    return [r["id"] for r in load_rules()]


def bicep_rule_fields() -> set[str]:
    """Every `rule.<field>` the deployment template reads."""
    return set(_BICEP_FIELD_RE.findall(BICEP_PATH.read_text()))


@pytest.fixture(params=rule_ids())
def rule(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Each rule as its own parametrised case, named by its id."""
    return next(r for r in load_rules() if r["id"] == request.param)


def parse_duration(value: str) -> timedelta:
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(f"not an ISO-8601 duration this parser handles: {value!r}")
    days, hours, minutes = (int(g) if g else 0 for g in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes)


def strip_strings(query: str) -> str:
    """Blank out string literals so their contents are not read as identifiers."""
    return _STRING_LITERAL_RE.sub('""', query)


def aliases_in(query: str) -> set[str]:
    """Names the query binds itself — `let` variables and summarize outputs."""
    return set(_ALIAS_RE.findall(strip_strings(query)))


class TestFileShape:
    def test_rules_file_exists_where_the_bicep_expects_it(self) -> None:
        # loadJsonContent('./sentinel-analytic-rules.json') resolves relative to
        # the .bicep, so both must sit in infra/.
        assert RULES_PATH.is_file()
        assert (RULES_PATH.parent / "sentinel-analytic-rules.bicep").is_file()

    def test_there_is_at_least_one_rule(self) -> None:
        assert load_rules()

    def test_rule_ids_are_unique(self) -> None:
        # The Bicep derives each rule's resource name from guid(workspace, id);
        # a duplicate id would silently collapse two rules into one resource.
        ids = rule_ids()
        assert len(ids) == len(set(ids))

    def test_rule_ids_are_stable_slugs(self) -> None:
        # Changing an id renames the Azure resource, orphaning the old rule.
        for rule_id in rule_ids():
            assert re.fullmatch(r"[a-z0-9-]+", rule_id), rule_id


class TestRequiredFields:
    def test_every_field_the_bicep_reads_is_present(self, rule) -> None:
        """Fields are derived from the Bicep, not hardcoded here.

        There is no Bicep compiler in this test environment, so this stands in
        for one: it reads every `rule.<field>` the template dereferences and
        checks the JSON supplies it. Adding a field to the template without
        adding it to the JSON is otherwise only discovered at deploy time, in
        the customer's subscription.
        """
        missing = sorted(f for f in bicep_rule_fields() if f not in rule)
        assert not missing, f"{rule['id']} missing {missing}, which the Bicep reads"

    def test_the_bicep_actually_reads_some_fields(self) -> None:
        # Guards the regex above: if it silently matched nothing, the check
        # would pass vacuously for every rule.
        assert len(bicep_rule_fields()) > 5

    def test_severity_is_one_sentinel_accepts(self, rule) -> None:
        assert rule["severity"] in SENTINEL_RULE_SEVERITIES

    def test_trigger_operator_is_valid(self, rule) -> None:
        assert rule["triggerOperator"] in {
            "GreaterThan",
            "LessThan",
            "Equal",
            "NotEqual",
        }

    def test_has_a_description_an_analyst_can_act_on(self, rule) -> None:
        # An alert with no explanation costs the analyst a research cycle every
        # time it fires.
        assert len(rule["description"]) > 80


class TestScheduling:
    def test_durations_parse(self, rule) -> None:
        for field in ("queryFrequency", "queryPeriod", "suppressionDuration"):
            parse_duration(rule[field])

    def test_query_period_is_within_sentinels_cap(self, rule) -> None:
        assert parse_duration(rule["queryPeriod"]) <= MAX_QUERY_PERIOD

    def test_query_period_covers_the_frequency(self, rule) -> None:
        # A period shorter than the frequency leaves gaps between runs where
        # events are never evaluated by anything.
        assert parse_duration(rule["queryPeriod"]) >= parse_duration(rule["queryFrequency"])

    def test_lookbacks_in_the_query_fit_inside_the_query_period(self, rule) -> None:
        # `ago(14d)` under a queryPeriod of 1d silently returns nothing: Sentinel
        # only hands the rule the last queryPeriod of data.
        period = parse_duration(rule["queryPeriod"])
        for amount, unit in re.findall(r"\b(\d+)([dhm])\b", rule["query"]):
            span = {
                "d": timedelta(days=int(amount)),
                "h": timedelta(hours=int(amount)),
                "m": timedelta(minutes=int(amount)),
            }[unit]
            assert span <= period, (
                f"{rule['id']} looks back {amount}{unit} but queryPeriod is {rule['queryPeriod']}"
            )


class TestQueryReferencesRealColumns:
    def test_query_targets_the_canonical_table(self, rule) -> None:
        assert sentinel_schema.TABLE_NAME in rule["query"]

    def test_every_identifier_is_a_column_or_a_local_alias(self, rule) -> None:
        """The core check: no rule may reference a column we do not emit."""
        known = {c.name for c in sentinel_schema.COLUMNS}
        known.add(sentinel_schema.TABLE_NAME)
        known |= aliases_in(rule["query"])
        known |= _KQL_ALLOWED_TOKENS

        found = set(_IDENTIFIER_RE.findall(strip_strings(rule["query"])))
        unknown = sorted(found - known)
        assert not unknown, (
            f"{rule['id']} references {unknown}, which are neither columns of "
            f"{sentinel_schema.TABLE_NAME} nor bound by the query. A rule like "
            "this deploys fine and then never fires."
        )

    def test_entity_mappings_use_columns_the_query_produces(self, rule) -> None:
        # Sentinel drops an entity mapping whose column is absent from the
        # result set, costing the incident its account pivot.
        available = {c.name for c in sentinel_schema.COLUMNS} | aliases_in(rule["query"])
        for mapping in rule["entityMappings"]:
            for field in mapping["fieldMappings"]:
                assert field["columnName"] in available, (
                    f"{rule['id']} maps entity field {field['identifier']} to "
                    f"{field['columnName']}, which the query does not produce"
                )

    def test_summarising_rules_still_emit_time_generated(self, rule) -> None:
        # Sentinel needs TimeGenerated on every result row to place the alert in
        # time; a summarize that drops it breaks correlation.
        if "summarize" not in rule["query"]:
            return
        assert "TimeGenerated" in aliases_in(rule["query"]) or re.search(
            r"by[^|]*\bTimeGenerated\b", rule["query"]
        ), f"{rule['id']} summarises without re-emitting TimeGenerated"


class TestQueryUsesRealEnumValues:
    def test_event_type_comparisons_use_real_wire_values(self, rule) -> None:
        # `EventType == "Blocked "` or `"blocked"` matches nothing, forever.
        valid = {e.value for e in SentinelEventType}
        for value in _EVENT_TYPE_RE.findall(rule["query"]):
            assert value in valid, (
                f"{rule['id']} compares EventType to {value!r}; the forwarder "
                f"only ever emits {sorted(valid)}"
            )

    def test_severity_comparisons_use_real_wire_values(self, rule) -> None:
        valid = {s.value for s in SentinelSeverity}
        for value in _SEVERITY_RE.findall(rule["query"]):
            assert value in valid, (
                f"{rule['id']} compares Severity to {value!r}; the forwarder "
                f"only ever emits {sorted(valid)}"
            )

    def test_the_forwarder_can_actually_produce_each_rules_trigger(self) -> None:
        """Every EventType a rule keys on must be one the mapper can emit.

        `Anonymised` is in the wire contract but deliberately unreachable from
        `prompt_events` (see sentinel_mapping.event_type_for), so a rule keyed
        on it would never fire no matter how the customer is configured.
        """
        emittable = {
            SentinelEventType.redacted.value,
            SentinelEventType.blocked.value,
            SentinelEventType.coached.value,
            SentinelEventType.bias_flagged.value,
        }
        for rule in load_rules():
            for value in _EVENT_TYPE_RE.findall(rule["query"]):
                assert value in emittable, (
                    f"{rule['id']} keys on EventType {value!r}, which the "
                    "mapper never emits — the rule could never fire"
                )


class TestIncidentConfiguration:
    def test_rules_create_incidents(self, rule) -> None:
        # A rule that raises an alert without an incident never reaches the
        # analyst queue, which is the whole point of shipping these.
        assert rule["incidentConfiguration"]["createIncident"] is True

    def test_grouping_by_entity_declares_that_entity(self, rule) -> None:
        grouping = rule["incidentConfiguration"].get("groupingConfiguration", {})
        if grouping.get("matchingMethod") != "Selected":
            return
        declared = {m["entityType"] for m in rule["entityMappings"]}
        for entity in grouping.get("groupByEntities", []):
            assert entity in declared, (
                f"{rule['id']} groups incidents by {entity} but maps no such "
                "entity, so every alert becomes its own incident"
            )


class TestGovernance:
    def test_no_rule_asks_for_prompt_content(self) -> None:
        """The product stance is structural: no column carries prompt text.

        A rule that projected one would be a review flag long before it was a
        bug, so assert the obvious rather than trust it.
        """
        banned = ("PromptText", "Prompt_Body", "PromptBody", "RawPrompt", "Content")
        for rule in load_rules():
            for token in banned:
                assert token not in rule["query"], f"{rule['id']} references {token}"

    def test_tactics_are_recognised_mitre_values(self, rule) -> None:
        # Sentinel rejects an unknown tactic at deploy time.
        valid = {
            "Reconnaissance",
            "ResourceDevelopment",
            "InitialAccess",
            "Execution",
            "Persistence",
            "PrivilegeEscalation",
            "DefenseEvasion",
            "CredentialAccess",
            "Discovery",
            "LateralMovement",
            "Collection",
            "CommandAndControl",
            "Exfiltration",
            "Impact",
            "ImpairProcessControl",
            "InhibitResponseFunction",
        }
        for tactic in rule["tactics"]:
            assert tactic in valid, f"{rule['id']} declares unknown tactic {tactic}"
