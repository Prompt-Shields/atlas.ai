"""Promptly Guide's adoption gate, as ported to Atlas (promptly-guide #27, #38).

The same promises as Guide's `AdoptionGateTests.swift`: a figure from fewer than
ten people is suppressed and not rounded, no figure is ever about all of a team
or none of it, and nothing a person typed can become a category.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.services import guide_adoption_service as svc
from app.services.guide_adoption_service import ContributionRejected, Tally

pytestmark = [pytest.mark.unit]

EVERYTHING = {"topic", "app", "completion"}
CLAUDE = ("app", "Claude")
CHATGPT = ("app", "ChatGPT")
OCTOBER = datetime(2026, 10, 5, tzinfo=UTC)


# ── The minimum group size ────────────────────────────────────────────────


def test_a_team_too_small_says_nothing_about_what_it_did() -> None:
    report = svc.gate([Tally("sales", 9, {CLAUDE: 9})], "2026-09", EVERYTHING)
    assert report.figures == []
    assert report.teams_too_small == ["sales"]
    assert report.suppressed_categories == {}


def test_a_figure_from_too_few_people_is_left_out_not_rounded() -> None:
    report = svc.gate([Tally("sales", 10, {CLAUDE: 9, CHATGPT: 10})], "2026-09", EVERYTHING)
    assert [f.category_id for f in report.figures] == ["ChatGPT"]
    assert report.suppressed_categories == {"sales": 1}


def test_only_offered_kinds_are_shown() -> None:
    topic = ("topic", "chatgpt/model")
    report = svc.gate([Tally("sales", 10, {topic: 10, CLAUDE: 10})], "2026-09", {"app"})
    assert [f.category_id for f in report.figures] == ["Claude"]


def test_a_team_named_twice_is_not_guessed_at() -> None:
    report = svc.gate(
        [Tally("sales", 10, {CLAUDE: 10}), Tally(" sales", 12, {CLAUDE: 12})],
        "2026-09",
        EVERYTHING,
    )
    assert report.figures == []
    assert report.unusable == 2


# ── Bands ─────────────────────────────────────────────────────────────────


def test_no_figure_ever_says_all_of_a_team_or_none_of_it() -> None:
    assert svc.band(0, 10).text == "under 20%"  # type: ignore[union-attr]
    assert svc.band(1, 10).text == "under 20%"  # type: ignore[union-attr]
    assert svc.band(8, 10).text == "80% or more"  # type: ignore[union-attr]
    assert svc.band(10, 10).text == "80% or more"  # type: ignore[union-attr]
    assert svc.band(5, 10).text == "50–59%"  # type: ignore[union-attr]
    assert svc.band(11, 10) is None
    assert svc.band(1, 0) is None


def test_bands_round_down() -> None:
    assert svc.band(29, 100).lower == 20  # type: ignore[union-attr]


# ── Periods ───────────────────────────────────────────────────────────────


def test_only_a_finished_recent_month_is_accepted() -> None:
    assert svc.acceptable_period("2026-09", OCTOBER) == "2026-09"
    assert svc.acceptable_period("2026-07", OCTOBER) == "2026-07"
    for refused in ("2026-10", "2026-11", "2026-06", "2026-13", "26-09", "2026-9"):
        with pytest.raises(ContributionRejected):
            svc.acceptable_period(refused, OCTOBER)


def test_january_looks_back_into_last_year() -> None:
    assert svc.acceptable_period("2025-12", datetime(2026, 1, 2, tzinfo=UTC)) == "2025-12"


# ── What a contribution may say ───────────────────────────────────────────


def test_a_contribution_is_trimmed_and_deduplicated() -> None:
    c = svc.validate_contribution(
        " sales ", "2026-09", [CLAUDE, CLAUDE, ("completion", "finished")], EVERYTHING, OCTOBER
    )
    assert c.team == "sales"
    assert c.categories == frozenset({CLAUDE, ("completion", "finished")})


@pytest.mark.parametrize(
    ("category", "offered"),
    [
        pytest.param(("app", "Claude"), {"topic"}, id="kind-not-offered"),
        pytest.param(("people", "Kari"), EVERYTHING, id="unknown-kind"),
        pytest.param(
            ("topic", "how do I tell my manager I am quitting"), EVERYTHING, id="free-text"
        ),
        pytest.param(("topic", "Chatgpt/Model"), EVERYTHING, id="topic-not-an-id"),
        pytest.param(("app", "x" * 41), EVERYTHING, id="app-too-long"),
        pytest.param(("completion", "finished!"), EVERYTHING, id="completion-not-an-id"),
    ],
)
def test_anything_but_a_compiled_in_identifier_is_refused(category, offered) -> None:
    with pytest.raises(ContributionRejected):
        svc.validate_contribution("sales", "2026-09", [category], offered, OCTOBER)


@pytest.mark.parametrize("team", ["", "   ", "x" * 101, "sales\nmarketing"])
def test_a_team_must_be_a_name(team: str) -> None:
    with pytest.raises(ContributionRejected):
        svc.validate_contribution(team, "2026-09", [], EVERYTHING, OCTOBER)


def test_an_organisation_that_offers_nothing_counts_nothing() -> None:
    with pytest.raises(ContributionRejected):
        svc.validate_contribution("sales", "2026-09", [], set(), OCTOBER)


def test_a_month_with_nothing_reached_is_still_a_contribution() -> None:
    c = svc.validate_contribution("sales", "2026-09", [], {"completion"}, OCTOBER)
    assert c.categories == frozenset()


# ── The receipt ───────────────────────────────────────────────────────────


def test_the_receipt_is_per_person_per_month_and_keyed() -> None:
    tenant = uuid.uuid4()
    a = svc.receipt("secret-one", tenant, "uid-1", "2026-09")
    assert a == svc.receipt("secret-one", tenant, "uid-1", "2026-09")
    assert a != svc.receipt("secret-one", tenant, "uid-1", "2026-08")
    assert a != svc.receipt("secret-one", tenant, "uid-2", "2026-09")
    assert a != svc.receipt("secret-two", tenant, "uid-1", "2026-09")
    assert "uid-1" not in a
    assert len(a) == 64
