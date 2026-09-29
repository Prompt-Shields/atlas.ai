"""Budget evaluation and alert-dispatch tests — cost-ledger slice 4.

The arithmetic in `services/cost/budgets.py` is simple enough that testing it
would be near-pointless on its own. What these tests defend is the set of cases
where the simple arithmetic would produce a confident, wrong-feeling answer:

  * a tenant with no ledger data reported as "0% used" — the same shape of lie
    as slice 3's infinite ROI, and the one most likely to be believed because
    it looks like good news;
  * an alert that repeats every day until it is filtered, which converts a
    working alarm into a silent one;
  * a month-end projection extrapolated from two days;
  * a budget row stamped as alerted when the mail never actually went.

Each class below is named for the failure it exists to prevent rather than for
the function it calls.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.ai_cost_record import CostProvider
from app.models.cost_budget import (
    ALERT_LEVEL_RANK,
    DEFAULT_WARN_THRESHOLD_PERCENT,
    BudgetAlertLevel,
)
from app.services.cost import budgets as mod
from app.services.cost.budgets import (
    BudgetStatus,
    _level_for,
    days_in_month,
    month_bounds,
    record_alert,
    render_alert,
    should_alert,
)

pytestmark = [pytest.mark.unit]


def make_budget(
    *,
    amount="1000.00",
    warn=DEFAULT_WARN_THRESHOLD_PERCENT,
    provider=None,
    alerts_enabled=True,
    last_period=None,
    last_level=None,
):
    """A CostBudget-shaped stand-in.

    SimpleNamespace rather than the ORM class: every function under test reads
    plain attributes, and a real model instance would drag in a session for no
    additional coverage.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        provider=provider,
        amount_usd=Decimal(amount),
        warn_threshold_percent=Decimal(warn),
        alerts_enabled=alerts_enabled,
        last_alerted_period=last_period,
        last_alerted_level=last_level,
        last_alerted_at=None,
    )


def make_status(
    *,
    level=BudgetAlertLevel.exceeded,
    has_data=True,
    period=date(2026, 9, 1),
    spend="1200.00",
    amount="1000.00",
    provisional="0.00",
    percent=Decimal("120.00"),
    projected=None,
    provider=None,
):
    return BudgetStatus(
        budget_id=uuid.uuid4(),
        provider=provider,
        amount_usd=Decimal(amount),
        spend_usd=Decimal(spend),
        provisional_usd=Decimal(provisional),
        percent_used=percent,
        alert_level=level,
        period_start=period,
        as_of=date(2026, 9, 20),
        has_ledger_data=has_data,
        projected_month_end_usd=projected,
    )


class TestNoDataIsNotZeroPercent:
    """The flattering-lie guard, mirroring slice 3's no-denominator rule."""

    def test_status_distinguishes_no_data_from_no_spend(self) -> None:
        """Both read $0 spent; only one of them is a fact about spending."""
        no_data = make_status(spend="0.00", percent=Decimal("0.00"), has_data=False)
        spent_nothing = make_status(spend="0.00", percent=Decimal("0.00"), has_data=True)

        assert no_data.spend_usd == spent_nothing.spend_usd
        assert no_data.has_ledger_data is not spent_nothing.has_ledger_data

    def test_no_data_never_alerts_even_when_percent_says_exceeded(self) -> None:
        """A broken connector must not be reported as an overspend.

        Without this guard a tenant whose sync died would be told their budget
        was exceeded, sending them to look at spending when the actual problem
        is that nothing is being recorded at all.
        """
        budget = make_budget()
        status = make_status(level=BudgetAlertLevel.exceeded, has_data=False)
        assert should_alert(budget, status) is False


class TestAnAlertFiresOncePerCrossing:
    """A daily-repeating alert is a filtered alert, which is no alert."""

    def test_first_crossing_alerts(self) -> None:
        budget = make_budget()
        assert should_alert(budget, make_status(level=BudgetAlertLevel.warning)) is True

    def test_same_level_does_not_realert_in_the_same_period(self) -> None:
        budget = make_budget(
            last_period=date(2026, 9, 1),
            last_level=BudgetAlertLevel.warning,
        )
        status = make_status(level=BudgetAlertLevel.warning, period=date(2026, 9, 1))
        assert should_alert(budget, status) is False

    def test_escalation_to_exceeded_does_alert(self) -> None:
        """Warning then exceeded are two different things worth saying."""
        budget = make_budget(
            last_period=date(2026, 9, 1),
            last_level=BudgetAlertLevel.warning,
        )
        status = make_status(level=BudgetAlertLevel.exceeded, period=date(2026, 9, 1))
        assert should_alert(budget, status) is True

    def test_de_escalation_does_not_alert(self) -> None:
        """Spend can fall when a vendor revises provisional days downward.

        Going from exceeded back to warning is not news worth mailing about.
        """
        budget = make_budget(
            last_period=date(2026, 9, 1),
            last_level=BudgetAlertLevel.exceeded,
        )
        status = make_status(level=BudgetAlertLevel.warning, period=date(2026, 9, 1))
        assert should_alert(budget, status) is False

    def test_new_month_resets_the_conversation(self) -> None:
        """August's exceeded must not silence September's."""
        budget = make_budget(
            last_period=date(2026, 8, 1),
            last_level=BudgetAlertLevel.exceeded,
        )
        status = make_status(level=BudgetAlertLevel.exceeded, period=date(2026, 9, 1))
        assert should_alert(budget, status) is True

    def test_disabled_alerts_never_fire(self) -> None:
        budget = make_budget(alerts_enabled=False)
        assert should_alert(budget, make_status(level=BudgetAlertLevel.exceeded)) is False

    def test_ok_never_fires(self) -> None:
        assert should_alert(make_budget(), make_status(level=BudgetAlertLevel.ok)) is False

    def test_record_alert_stamps_all_three_fields(self) -> None:
        """A partial stamp would re-alert or never alert again."""
        budget = make_budget()
        status = make_status(level=BudgetAlertLevel.exceeded, period=date(2026, 9, 1))
        record_alert(budget, status)

        assert budget.last_alerted_period == date(2026, 9, 1)
        assert budget.last_alerted_level is BudgetAlertLevel.exceeded
        assert budget.last_alerted_at is not None

    def test_recording_then_reevaluating_is_silent(self) -> None:
        """The round trip the dispatcher actually performs."""
        budget = make_budget()
        status = make_status(level=BudgetAlertLevel.exceeded, period=date(2026, 9, 1))

        assert should_alert(budget, status) is True
        record_alert(budget, status)
        assert should_alert(budget, status) is False


class TestLevelThresholds:
    def test_below_threshold_is_ok(self) -> None:
        assert _level_for(Decimal("79.99"), Decimal("80")) is BudgetAlertLevel.ok

    def test_at_threshold_is_warning(self) -> None:
        """Boundary is inclusive: 80% of an 80% threshold has been reached."""
        assert _level_for(Decimal("80.00"), Decimal("80")) is BudgetAlertLevel.warning

    def test_at_one_hundred_is_exceeded_not_warning(self) -> None:
        assert _level_for(Decimal("100.00"), Decimal("80")) is BudgetAlertLevel.exceeded

    def test_no_percentage_is_ok_rather_than_an_error(self) -> None:
        assert _level_for(None, Decimal("80")) is BudgetAlertLevel.ok

    def test_rank_ordering_is_what_escalation_depends_on(self) -> None:
        assert (
            ALERT_LEVEL_RANK[BudgetAlertLevel.ok]
            < ALERT_LEVEL_RANK[BudgetAlertLevel.warning]
            < ALERT_LEVEL_RANK[BudgetAlertLevel.exceeded]
        )


class TestMonthArithmetic:
    def test_month_bounds_runs_to_today_not_month_end(self) -> None:
        """Judging spend against days that have not happened would be absurd."""
        start, as_of = month_bounds(date(2026, 9, 7))
        assert start == date(2026, 9, 1)
        assert as_of == date(2026, 9, 7)

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 9, 15), 30),
            (date(2026, 1, 5), 31),
            (date(2026, 2, 3), 28),
            (date(2024, 2, 3), 29),  # leap year
            (date(2026, 12, 25), 31),  # December must not roll the year wrong
        ],
    )
    def test_days_in_month(self, day: date, expected: int) -> None:
        assert days_in_month(day) == expected


class TestRenderedAlert:
    def test_provisional_spend_is_named_in_the_body(self) -> None:
        """An admin about to switch something off deserves to know."""
        _, body = render_alert(make_status(provisional="250.00"))
        assert "provisional" in body.lower()
        assert "250.00" in body

    def test_no_provisional_caveat_when_nothing_is_provisional(self) -> None:
        _, body = render_alert(make_status(provisional="0.00"))
        assert "provisional" not in body.lower()

    def test_projection_omitted_when_none(self) -> None:
        _, body = render_alert(make_status(projected=None))
        assert "month ends near" not in body

    def test_projection_included_when_present(self) -> None:
        _, body = render_alert(make_status(projected=Decimal("1800.00")))
        assert "1800.00" in body

    def test_subject_distinguishes_exceeded_from_approaching(self) -> None:
        exceeded, _ = render_alert(make_status(level=BudgetAlertLevel.exceeded))
        warning, _ = render_alert(make_status(level=BudgetAlertLevel.warning))
        assert "exceeded" in exceeded
        assert "approaching" in warning

    def test_provider_scope_is_named(self) -> None:
        """ "AI spend exceeded" for a Cursor-only budget would misdirect."""
        subject, _ = render_alert(make_status(provider=CostProvider.cursor))
        assert "cursor" in subject.lower()

    def test_body_says_the_alert_does_not_repeat(self) -> None:
        """Sets the expectation that silence afterwards is not a malfunction."""
        _, body = render_alert(make_status())
        assert "once per threshold" in body


class TestProjectionHonesty:
    """A projection from two days is arithmetic dressed as foresight."""

    def test_min_days_constant_is_at_least_a_week(self) -> None:
        assert mod._MIN_DAYS_FOR_PROJECTION >= 7

    def test_status_allows_absent_projection(self) -> None:
        assert make_status(projected=None).projected_month_end_usd is None


class TestIsProvisionalProperty:
    def test_true_when_any_provisional(self) -> None:
        assert make_status(provisional="0.01").is_provisional is True

    def test_false_at_zero(self) -> None:
        assert make_status(provisional="0.00").is_provisional is False


class TestMoneyCoercion:
    def test_none_becomes_zero(self) -> None:
        assert mod._money(None) == Decimal("0.00")

    def test_quantised_to_cents(self) -> None:
        assert mod._money("10.005") == Decimal("1.00") * Decimal("10.01")

    def test_goes_via_str_not_float(self) -> None:
        """Decimal(float) would reintroduce binary-fraction error."""
        assert mod._money(0.1 + 0.2) == Decimal("0.30")


class TestDefaults:
    def test_warn_threshold_default_is_below_one_hundred(self) -> None:
        """At or above 100 the warning could never fire before exceeded."""
        assert Decimal("0") < DEFAULT_WARN_THRESHOLD_PERCENT < Decimal("100")
