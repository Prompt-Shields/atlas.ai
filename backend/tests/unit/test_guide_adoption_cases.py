"""The adoption gate's shared cases, run against Atlas's port (promptly-guide #27, #282).

`tests/contracts/adoption-cases.json` is a byte-for-byte copy of promptly-guide's
`contracts/adoption-cases.json`, the gate's rules as cases that the Mac and Windows run
too. Atlas's port is the one that shows an employer the figures, so it is held to the
same cases: a band, a report or an intake answer that drifts from Guide's fails here.
Change the file in promptly-guide first, then copy it here (promptly-guide's
`Scripts/check-atlas-contract.py --atlas` says whether the copies match).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.services import guide_adoption_service as svc
from app.services.guide_adoption_service import ContributionRejected, Tally

pytestmark = [pytest.mark.unit]

CASES = json.loads(
    (Path(__file__).resolve().parent.parent / "contracts" / "adoption-cases.json").read_text(
        encoding="utf-8"
    )
)

# Sections that are the clients' rules, not Atlas's, and why. A new section in the file
# fails `test_every_section_is_run_or_said_why_not` until it is run here or listed here.
NOT_ATLAS = {
    # Atlas checks only the shape of an id, on purpose: Guide makes categories from
    # compiled-in identifiers, and a list in Atlas would need an Atlas release for every
    # tool Guide learns. The gate still applies to whatever arrives
    # (promptly-guide docs/adoption-analytics.md, "One set of rules on three sides").
    "categories",
    # Guide's `AdoptionGate.refusal` explains one category to the person on the Mac.
    # Atlas has no such call; the same rules reach it through `reports`.
    "refusals",
    # What the Mac decides to send, after asking the person. Atlas refuses a kind it
    # does not offer rather than dropping it (`validate_contribution`).
    "contributions",
    # Atlas folds contributions into running counts in the database
    # (`record_contribution`); there is no in-memory tally to compare.
    "tallies",
    # Which month the Mac sends for. Atlas's side of it is `intake`.
    "last_finished",
}
RUN_HERE = {"bands", "reports", "intake"}


def _category(stored: str) -> tuple[str, str]:
    kind, _, category_id = stored.partition(":")
    return kind, category_id


def _tally(case: dict) -> Tally:
    return Tally(
        case["team"],
        case["contributors"],
        {_category(stored): people for stored, people in case["people"].items()},
    )


def _name(case: dict) -> str:
    return case["name"]


def test_every_section_is_run_or_said_why_not() -> None:
    sections = set(CASES) - {"about"}
    assert sections == RUN_HERE | NOT_ATLAS
    for section in RUN_HERE:
        assert CASES[section], f"{section} has no cases"


@pytest.mark.parametrize("case", CASES["bands"], ids=lambda c: f"{c['count']}of{c['total']}")
def test_band(case: dict) -> None:
    shown = svc.band(case["count"], case["total"])
    assert (shown.text if shown else None) == case["text"]


@pytest.mark.parametrize("case", CASES["reports"], ids=_name)
def test_report(case: dict) -> None:
    report = svc.gate([_tally(t) for t in case["tallies"]], "2026-09", set(case["kinds"]))
    figures = [
        {"team": f.team, "category": f"{f.category_kind}:{f.category_id}", "band": f.band.text}
        for f in report.figures
    ]
    assert figures == case["figures"]
    assert report.teams_too_small == case["teams_too_small"]
    assert report.suppressed_categories == case["suppressed"]
    assert report.unusable == case["unusable"]


@pytest.mark.parametrize("case", CASES["intake"], ids=lambda c: f"{c['period']}@{c['now']}")
def test_intake(case: dict) -> None:
    now = datetime.fromisoformat(case["now"])
    try:
        accepted = svc.acceptable_period(case["period"], now) == case["period"]
    except ContributionRejected:
        accepted = False
    assert accepted is case["open"]
    # The report is shown exactly when intake has closed for a finished month.
    if case["open"]:
        assert not svc.is_closed(case["period"], now)
