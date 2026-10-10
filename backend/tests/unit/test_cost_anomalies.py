"""Anomaly detection tests — cost-ledger slice 5.

A detector's failure mode is not missing a spike; it is crying often enough
that someone mutes it, after which it misses every spike and reports success.
So these tests are weighted heavily toward the cases that *must not* fire.

Each class is named for the false positive it prevents. The one class that
tests true positives exists mainly to prove the guards have not been tightened
into a detector that can never fire at all — the opposite failure, and the one
a suite full of negative assertions would happily let through.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.ai_cost_record import (
    AICostRecord,
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
)
from app.models.integration import Integration, IntegrationProvider, IntegrationStatus
from app.services.cost import anomalies as mod
from app.services.cost.anomalies import (
    BASELINE_WINDOW_DAYS,
    DEFAULT_RATIO_THRESHOLD,
    MIN_ABSOLUTE_DELTA_USD,
    MIN_BASELINE_DAYS,
    evaluate_series,
)
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit]

AS_OF = date(2026, 9, 14)


def series(history: list[str], today: str, *, end: date = AS_OF) -> dict[date, Decimal]:
    """Build a daily series ending at `end`, with `history` immediately before.

    History is laid out backwards from the day before `end`, so the list reads
    oldest-last but always lands adjacent to the evaluated day — no accidental
    gaps that would silently shrink the baseline window.
    """
    out = {end: Decimal(today)}
    for i, v in enumerate(history, start=1):
        out[end - timedelta(days=i)] = Decimal(v)
    return out


def flat(value: str, days: int) -> list[str]:
    return [value] * days


class TestQuietWhenItShould:
    """Every one of these would be a false positive, and each has a reason."""

    def test_no_history_is_not_an_anomaly(self) -> None:
        """Three numbers are a sample, not a baseline."""
        assert evaluate_series(series(["100", "100"], "9999"), provider=None, as_of=AS_OF) is None

    def test_one_day_short_of_the_minimum_stays_quiet(self) -> None:
        """Boundary: the guard must bite at exactly MIN_BASELINE_DAYS - 1."""
        s = series(flat("100", MIN_BASELINE_DAYS - 1), "9999")
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None

    def test_small_absolute_jump_is_not_an_anomaly(self) -> None:
        """$0.02 -> $0.30 is 15x and means nothing.

        Without the absolute floor the detector's output is dominated by the
        tenants who spend the least, which is precisely backwards.
        """
        s = series(flat("0.02", 14), "0.30")
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None

    def test_ordinary_variation_is_not_an_anomaly(self) -> None:
        """A normal-looking week with a mild uptick must stay silent."""
        s = series(["100", "120", "95", "110", "105", "130", "90", "115", "100", "125"], "150")
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None

    def test_zero_baseline_yields_nothing_rather_than_infinity(self) -> None:
        """First-ever spend is a start, not a spike.

        Same shape as the ROI no-denominator rule: the honest answer to
        "x times zero" is that there is no comparison, not a large number.
        """
        s = series(flat("0", 14), "500")
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None

    def test_missing_evaluated_day_is_not_an_anomaly(self) -> None:
        """A day with no finalized rows is unknown, not zero."""
        s = series(flat("100", 14), "0")
        del s[AS_OF]
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None

    def test_a_drop_is_never_an_anomaly(self) -> None:
        """This detector is about overspend. A collapse to near zero may well
        be a broken connector, but calling it a spend anomaly would point the
        reader at the wrong thing entirely."""
        s = series(flat("500", 14), "5")
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None

    def test_days_outside_the_window_do_not_count_toward_the_baseline(self) -> None:
        """Old history must not satisfy the minimum-days guard.

        Built with a deliberate gap: plenty of days exist, but they sit before
        the window, so the effective baseline is too short and nothing fires.
        """
        s = {AS_OF: Decimal("9999")}
        for i in range(BASELINE_WINDOW_DAYS + 1, BASELINE_WINDOW_DAYS + 20):
            s[AS_OF - timedelta(days=i)] = Decimal("100")
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None


class TestTheMedianIsLoadBearing:
    """The single most consequential decision in the module."""

    def test_an_earlier_spike_does_not_mask_the_next_one(self) -> None:
        """The case a mean would miss.

        Fourteen quiet days plus one huge earlier spike. The mean of that
        window is dragged up far enough that today's genuine spike falls under
        3x; the median ignores the outlier and fires. This is the same runaway
        process spiking twice — exactly when you most need the second alert.
        """
        history = [*flat("100", 13), "5000"]
        s = series(history, "400")

        mean = sum(Decimal(h) for h in history) / len(history)
        assert Decimal("400") / mean < DEFAULT_RATIO_THRESHOLD, "mean would not fire"

        found = evaluate_series(s, provider=None, as_of=AS_OF)
        assert found is not None, "median must still fire"
        assert found.baseline_usd == Decimal("100.00")


class TestFiresWhenItShould:
    """Guards tightened into a detector that never fires would pass every
    test above. These stop that."""

    def test_a_clear_spike_is_reported(self) -> None:
        found = evaluate_series(series(flat("100", 14), "600"), provider=None, as_of=AS_OF)
        assert found is not None
        assert found.observed_usd == Decimal("600.00")
        assert found.baseline_usd == Decimal("100.00")
        assert found.ratio == Decimal("6.00")
        assert found.baseline_days == 14

    def test_exactly_at_the_ratio_threshold_fires(self) -> None:
        """Boundary is inclusive — 3x of a 3x threshold has been reached."""
        found = evaluate_series(series(flat("100", 14), "300"), provider=None, as_of=AS_OF)
        assert found is not None
        assert found.ratio == DEFAULT_RATIO_THRESHOLD.quantize(Decimal("0.01"))

    def test_just_under_the_ratio_threshold_does_not(self) -> None:
        assert evaluate_series(series(flat("100", 14), "299"), provider=None, as_of=AS_OF) is None

    def test_exactly_at_the_minimum_baseline_fires(self) -> None:
        s = series(flat("100", MIN_BASELINE_DAYS), "600")
        found = evaluate_series(s, provider=None, as_of=AS_OF)
        assert found is not None
        assert found.baseline_days == MIN_BASELINE_DAYS

    def test_provider_is_carried_onto_the_finding(self) -> None:
        """A tenant-wide alert for a Cursor-only spike would misdirect."""
        found = evaluate_series(
            series(flat("100", 14), "600"), provider=CostProvider.cursor, as_of=AS_OF
        )
        assert found is not None
        assert found.provider is CostProvider.cursor

    def test_a_custom_threshold_is_honoured(self) -> None:
        s = series(flat("100", 14), "250")
        assert evaluate_series(s, provider=None, as_of=AS_OF) is None
        found = evaluate_series(s, provider=None, as_of=AS_OF, ratio_threshold=Decimal("2.0"))
        assert found is not None


class TestThresholdsAreSane:
    """The constants are policy; these assert the policy is coherent."""

    def test_window_is_longer_than_the_minimum(self) -> None:
        assert BASELINE_WINDOW_DAYS > MIN_BASELINE_DAYS

    def test_minimum_baseline_is_at_least_a_week(self) -> None:
        """Shorter than a week and a Monday is compared to no other Monday."""
        assert MIN_BASELINE_DAYS >= 7

    def test_ratio_threshold_is_above_one(self) -> None:
        """At or below 1x every ordinary day is an anomaly."""
        assert DEFAULT_RATIO_THRESHOLD > Decimal("1")

    def test_absolute_floor_is_positive(self) -> None:
        assert MIN_ABSOLUTE_DELTA_USD > 0


class TestRenderedAlert:
    def test_body_states_what_normal_means(self) -> None:
        """ "5x normal" without a baseline invites disbelief, then muting."""
        from types import SimpleNamespace

        row = SimpleNamespace(
            provider=CostProvider.cursor,
            usage_date=date(2026, 9, 14),
            observed_usd=Decimal("600.00"),
            baseline_usd=Decimal("100.00"),
            ratio=Decimal("6.00"),
            baseline_days=14,
        )
        subject, body = mod.render_anomaly_alert(row)
        assert "cursor" in subject.lower()
        assert "6.00" in subject
        assert "100.00" in body
        assert "14 days" in body
        # Says the figure is settled, so nobody dismisses it as partial data.
        assert "finalized" in body


# ─── Guard 4, which lives in SQL rather than in evaluate_series ──────
#
# Everything above exercises the pure function, so none of it touches the
# `is_provisional` filter. Mutation testing caught that: removing the filter
# left all twenty tests green. These go through the database.


@pytest_asyncio.fixture
async def db(setup_database):
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID, name="Test Tenant")
        yield session


async def _integration(db) -> Integration:
    row = Integration(
        tenant_id=TEST_TENANT_ID,
        provider=IntegrationProvider.CURSOR,
        display_name="Cursor",
        status=IntegrationStatus.CONNECTED,
    )
    db.add(row)
    await db.flush()
    return row


async def _record(db, integration, day: date, cost: str, *, provisional: bool) -> None:
    db.add(
        AICostRecord(
            tenant_id=TEST_TENANT_ID,
            integration_id=integration.id,
            provider=CostProvider.cursor,
            usage_date=day,
            cost_kind=CostKind.metered_usage,
            subject_kind=CostSubjectKind.model,
            subject_ref=f"m-{day}-{provisional}",
            cost_source=CostSource.vendor_reported,
            cost_usd=Decimal(cost),
            is_provisional=provisional,
        )
    )
    await db.flush()


class TestProvisionalDaysAreExcluded:
    """A partially reported day is unknown, not small.

    Averaging an incomplete day into the baseline drags it down and
    manufactures an anomaly for the next ordinary day — and evaluating a
    partial day directly produces an alert that un-fires by evening, which is
    the flapping that teaches people to ignore the channel.
    """

    async def test_provisional_rows_are_not_in_the_series(self, db) -> None:
        integration = await _integration(db)
        await _record(db, integration, date(2026, 9, 10), "100", provisional=False)
        await _record(db, integration, date(2026, 9, 11), "900", provisional=True)

        daily = await mod._finalized_daily(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=CostProvider.cursor,
            since=date(2026, 9, 1),
            until=date(2026, 9, 30),
        )

        assert daily == {date(2026, 9, 10): Decimal("100.00")}
        assert date(2026, 9, 11) not in daily, "provisional day leaked into the baseline"

    async def test_a_provisional_spike_does_not_become_an_anomaly(self, db) -> None:
        """End to end: the spike is real in the ledger but not yet final."""
        integration = await _integration(db)
        spike_day = date(2026, 9, 20)
        for i in range(1, 15):
            await _record(db, integration, spike_day - timedelta(days=i), "100", provisional=False)
        await _record(db, integration, spike_day, "5000", provisional=True)

        daily = await mod._finalized_daily(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=CostProvider.cursor,
            since=spike_day - timedelta(days=BASELINE_WINDOW_DAYS),
            until=spike_day,
        )
        assert evaluate_series(daily, provider=CostProvider.cursor, as_of=spike_day) is None

        # ...and once the vendor finalizes the same day, it is reported.
        row = (
            await db.execute(
                select(AICostRecord).where(
                    AICostRecord.usage_date == spike_day,
                    AICostRecord.tenant_id == TEST_TENANT_ID,
                )
            )
        ).scalar_one()
        row.is_provisional = False
        await db.flush()

        daily = await mod._finalized_daily(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=CostProvider.cursor,
            since=spike_day - timedelta(days=BASELINE_WINDOW_DAYS),
            until=spike_day,
        )
        found = evaluate_series(daily, provider=CostProvider.cursor, as_of=spike_day)
        assert found is not None, "a finalized spike must be reported"
        assert found.observed_usd == Decimal("5000.00")

    async def test_another_tenants_rows_are_not_in_the_series(self, db) -> None:
        """The explicit tenant filter, independent of RLS."""
        integration = await _integration(db)
        await _record(db, integration, date(2026, 9, 10), "100", provisional=False)

        other = uuid.uuid4()
        daily = await mod._finalized_daily(
            db,
            tenant_id=other,
            provider=CostProvider.cursor,
            since=date(2026, 9, 1),
            until=date(2026, 9, 30),
        )
        assert daily == {}
