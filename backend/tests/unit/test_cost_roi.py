"""AI ROI — cost-ledger slice 3.

Most of this file tests guards rather than arithmetic, and deliberately so. The
arithmetic is two multiplications; what makes an ROI feature trustworthy or not
is what it does at the edges — no spend, no data, a loss, a missing assumption
— because every one of those has a flattering wrong answer that would look
perfectly reasonable on a dashboard.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models.ai_cost_record import (
    AICostRecord,
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
)
from app.models.integration import Integration, IntegrationProvider, IntegrationStatus
from app.models.roi_assumptions import (
    DEFAULT_BLENDED_HOURLY_RATE_USD,
    HoursSavedSource,
    RoiAssumptions,
)
from app.services.cost.roi import (
    HoursSaved,
    HoursSavedBasis,
    build_roi,
    compute_roi,
    get_or_default_assumptions,
    ledger_spend_usd,
    resolve_hours_saved,
    seeded_hours_saved_per_month,
)
from tests.conftest import (
    TEST_TENANT_ID,
    TestSessionLocal,
    auth_header,
    ensure_tenant,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JAN = date(2026, 1, 1)
JAN_END = date(2026, 1, 31)


def hours(value: str, basis: HoursSavedBasis = HoursSavedBasis.measured) -> HoursSaved:
    return HoursSaved(hours_per_month=Decimal(value), basis=basis, detail="test")


@pytest_asyncio.fixture
async def db(setup_database):
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID, name="Test Tenant")
        yield session


async def seed_spend(db, amount: str, *, usage_date: date = JAN) -> None:
    """One ledger row, with the integration it hangs off."""
    integration = (
        await db.execute(
            select(Integration).where(
                Integration.tenant_id == TEST_TENANT_ID,
                Integration.provider == IntegrationProvider.ANTHROPIC,
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        integration = Integration(
            tenant_id=TEST_TENANT_ID,
            provider=IntegrationProvider.ANTHROPIC,
            display_name="Anthropic",
            status=IntegrationStatus.CONNECTED,
            is_active=True,
        )
        db.add(integration)
        await db.flush()

    db.add(
        AICostRecord(
            tenant_id=TEST_TENANT_ID,
            integration_id=integration.id,
            provider=CostProvider.anthropic,
            usage_date=usage_date,
            cost_kind=CostKind.metered_usage,
            subject_kind=CostSubjectKind.model,
            subject_ref=f"claude-sonnet-4-{usage_date}",
            cost_usd=Decimal(amount),
            cost_source=CostSource.vendor_reported,
            is_provisional=False,
            ingested_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    )
    await db.flush()


class TestNoDenominator:
    """The guard that matters most.

    A tenant with no connected cost connector has zero spend in the window. The
    ratio is undefined there, and every convenient stand-in — 0, a huge number,
    float('inf') — renders on a dashboard as a claim the product cannot
    support. "Infinite ROI" is one unguarded division away.
    """

    def test_zero_spend_yields_no_multiplier_rather_than_infinity(self) -> None:
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("0"),
            hours_saved=hours("100"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.roi_multiplier is None

    def test_zero_spend_still_reports_the_value_and_net(self) -> None:
        """None means "undefined ratio", not "we computed nothing"."""
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("0"),
            hours_saved=hours("100"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.human_value_usd > 0
        assert result.net_value_usd == result.human_value_usd

    def test_the_smallest_real_spend_does_produce_a_multiplier(self) -> None:
        """The guard must key on zero, not on "small"."""
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("0.01"),
            hours_saved=hours("100"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.roi_multiplier is not None
        assert result.roi_multiplier > 0


class TestALossIsReportedAsALoss:
    def test_net_value_goes_negative_when_ai_costs_more_than_it_saves(self) -> None:
        """No floor at zero.

        A tool that cannot show that AI is costing more than it saves is a
        marketing asset, not a cost tool. This is the case a customer most
        needs to see.
        """
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("10000"),
            hours_saved=hours("1"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.net_value_usd < 0

    def test_multiplier_below_one_is_reported_not_clamped(self) -> None:
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("10000"),
            hours_saved=hours("1"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.roi_multiplier is not None
        assert result.roi_multiplier < 1


class TestProvenanceIsNeverLost:
    """`is_illustrative` is what the UI badge hangs off.

    If it can ever be False while the underlying figure is a sample or a guess,
    the product presents an illustration as a finding.
    """

    @pytest.mark.parametrize(
        "basis",
        [HoursSavedBasis.sampled, HoursSavedBasis.manual],
    )
    def test_non_measured_bases_are_always_flagged(self, basis) -> None:
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("100"),
            hours_saved=hours("10", basis),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.is_illustrative is True

    def test_only_measured_clears_the_flag(self) -> None:
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("100"),
            hours_saved=hours("10", HoursSavedBasis.measured),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.is_illustrative is False

    def test_every_basis_is_covered_by_the_flag_rule(self) -> None:
        """Guards against a new basis silently defaulting to "not illustrative".

        Adding an enum member is the realistic way this invariant breaks, so
        the test enumerates the enum rather than a hand-written list.
        """
        for basis in HoursSavedBasis:
            result = compute_roi(
                window_start=JAN,
                window_end=JAN_END,
                ai_spend_usd=Decimal("100"),
                hours_saved=hours("10", basis),
                blended_hourly_rate_usd=Decimal("75"),
            )
            assert result.is_illustrative == (basis is not HoursSavedBasis.measured), (
                f"{basis.value}: illustrative flag does not match the basis"
            )

    def test_the_basis_detail_survives_to_the_result(self) -> None:
        """The badge needs a sentence, not just a boolean."""
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("100"),
            hours_saved=HoursSaved(
                hours_per_month=Decimal("10"),
                basis=HoursSavedBasis.sampled,
                detail="a specific explanation",
            ),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.basis_detail == "a specific explanation"


class TestWindowArithmetic:
    def test_window_is_inclusive_of_both_endpoints(self) -> None:
        """A single-day window is one day, not zero.

        An off-by-one here scales every ROI figure in the product, and would be
        invisible — the numbers would simply be slightly wrong forever.
        """
        result = compute_roi(
            window_start=JAN,
            window_end=JAN,
            ai_spend_usd=Decimal("10"),
            hours_saved=hours("30"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.window_days == 1
        assert result.hours_saved_in_window > 0

    def test_a_month_window_yields_roughly_the_monthly_figure(self) -> None:
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("10"),
            hours_saved=hours("100"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert result.window_days == 31
        # 31/30.4375 of a month.
        assert Decimal("100") < result.hours_saved_in_window < Decimal("103")

    def test_longer_windows_scale_up(self) -> None:
        one_month = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("10"),
            hours_saved=hours("100"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        one_year = compute_roi(
            window_start=JAN,
            window_end=date(2026, 12, 31),
            ai_spend_usd=Decimal("10"),
            hours_saved=hours("100"),
            blended_hourly_rate_usd=Decimal("75"),
        )
        assert one_year.hours_saved_in_window > one_month.hours_saved_in_window * 11

    def test_a_reversed_window_raises_rather_than_returning_a_negative(self) -> None:
        """Silently producing negative hours would invert the whole result."""
        with pytest.raises(ValueError, match="must not precede"):
            compute_roi(
                window_start=JAN_END,
                window_end=JAN,
                ai_spend_usd=Decimal("10"),
                hours_saved=hours("100"),
                blended_hourly_rate_usd=Decimal("75"),
            )


class TestMoney:
    def test_values_are_rounded_to_cents(self) -> None:
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("33.333333"),
            hours_saved=hours("7.77"),
            blended_hourly_rate_usd=Decimal("83.33"),
        )
        for value in (
            result.ai_spend_usd,
            result.human_value_usd,
            result.net_value_usd,
        ):
            assert value == value.quantize(Decimal("0.01"))

    def test_net_value_is_consistent_with_its_parts(self) -> None:
        result = compute_roi(
            window_start=JAN,
            window_end=JAN_END,
            ai_spend_usd=Decimal("1234.56"),
            hours_saved=hours("40"),
            blended_hourly_rate_usd=Decimal("90"),
        )
        assert result.net_value_usd == result.human_value_usd - result.ai_spend_usd


class TestResolveHoursSaved:
    def test_manual_source_with_a_figure_is_used_and_labelled_manual(self) -> None:
        assumptions = RoiAssumptions(
            tenant_id=TEST_TENANT_ID,
            blended_hourly_rate_usd=Decimal("75"),
            hours_saved_source=HoursSavedSource.manual,
            manual_hours_saved_per_month=Decimal("250"),
        )
        resolved = resolve_hours_saved(assumptions)
        assert resolved.hours_per_month == Decimal("250")
        assert resolved.basis is HoursSavedBasis.manual

    def test_manual_source_without_a_figure_does_not_become_zero(self) -> None:
        """The bug this branch exists to prevent.

        Treating "not supplied" as 0 hours would render a real, terrible ROI —
        a confident claim that AI saved nothing — when the truth is that nobody
        has answered the question yet.
        """
        assumptions = RoiAssumptions(
            tenant_id=TEST_TENANT_ID,
            blended_hourly_rate_usd=Decimal("75"),
            hours_saved_source=HoursSavedSource.manual,
            manual_hours_saved_per_month=None,
        )
        resolved = resolve_hours_saved(assumptions)
        assert resolved.hours_per_month > 0
        assert resolved.basis is HoursSavedBasis.sampled

    def test_pipeline_source_is_labelled_sampled_not_measured(self) -> None:
        """The pipeline reads a seeded sample, not this tenant's usage.

        Labelling it `measured` would be the single most misleading line in the
        feature: the customer would read a representative fiction as their own
        result.
        """
        assumptions = RoiAssumptions(
            tenant_id=TEST_TENANT_ID,
            blended_hourly_rate_usd=Decimal("75"),
            hours_saved_source=HoursSavedSource.adoption_pipeline,
            manual_hours_saved_per_month=None,
        )
        resolved = resolve_hours_saved(assumptions)
        assert resolved.basis is HoursSavedBasis.sampled

    def test_the_sampled_detail_says_it_is_not_the_tenants_own_data(self) -> None:
        assumptions = RoiAssumptions(
            tenant_id=TEST_TENANT_ID,
            blended_hourly_rate_usd=Decimal("75"),
            hours_saved_source=HoursSavedSource.adoption_pipeline,
        )
        detail = resolve_hours_saved(assumptions).detail.lower()
        assert "not from this organisation" in detail

    def test_seeded_figure_is_positive_and_plausible(self) -> None:
        """A sanity bound, not a golden value.

        Pinning the exact number would break whenever the sample changes, which
        is not a regression. But a zero or a wildly large figure would be.
        """
        assert Decimal("1") < seeded_hours_saved_per_month() < Decimal("10000")


class TestAssumptionsDefaults:
    async def test_a_tenant_with_no_row_gets_the_defaults(self, db) -> None:
        assumptions = await get_or_default_assumptions(db, TEST_TENANT_ID)
        assert assumptions.blended_hourly_rate_usd == DEFAULT_BLENDED_HOURLY_RATE_USD
        assert assumptions.hours_saved_source is HoursSavedSource.adoption_pipeline

    async def test_reading_the_defaults_does_not_persist_a_row(self, db) -> None:
        """Viewing must not look like configuring.

        A row written on read would show up in an audit as though somebody had
        set the numbers behind the headline, and would mask the "nobody has
        configured this" state the UI needs to prompt on.
        """
        await get_or_default_assumptions(db, TEST_TENANT_ID)
        await db.flush()
        stored = (
            await db.execute(
                select(RoiAssumptions).where(RoiAssumptions.tenant_id == TEST_TENANT_ID)
            )
        ).scalar_one_or_none()
        assert stored is None

    async def test_a_stored_row_overrides_the_defaults(self, db) -> None:
        db.add(
            RoiAssumptions(
                tenant_id=TEST_TENANT_ID,
                blended_hourly_rate_usd=Decimal("142.50"),
                hours_saved_source=HoursSavedSource.manual,
                manual_hours_saved_per_month=Decimal("500"),
            )
        )
        await db.flush()
        assumptions = await get_or_default_assumptions(db, TEST_TENANT_ID)
        assert assumptions.blended_hourly_rate_usd == Decimal("142.50")


class TestLedgerSpendIsTheMeasuredHalf:
    async def test_sums_rows_inside_the_window(self, db) -> None:
        await seed_spend(db, "10.00", usage_date=date(2026, 1, 10))
        await seed_spend(db, "15.50", usage_date=date(2026, 1, 20))
        total = await ledger_spend_usd(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert total == Decimal("25.50")

    async def test_excludes_rows_outside_the_window(self, db) -> None:
        await seed_spend(db, "10.00", usage_date=date(2026, 1, 10))
        await seed_spend(db, "999.00", usage_date=date(2026, 2, 10))
        total = await ledger_spend_usd(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert total == Decimal("10.00")

    async def test_window_bounds_are_inclusive(self, db) -> None:
        await seed_spend(db, "1.00", usage_date=JAN)
        await seed_spend(db, "2.00", usage_date=JAN_END)
        total = await ledger_spend_usd(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert total == Decimal("3.00")

    async def test_no_rows_is_zero_not_none(self, db) -> None:
        """A None here would propagate into the ratio guard as a crash."""
        total = await ledger_spend_usd(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert total == Decimal("0")

    async def test_another_tenants_spend_is_not_counted(self, db) -> None:
        """Filtered on tenant_id in the query, not left to RLS.

        A leak here would not merely expose the other tenant's spend — it would
        silently corrupt this tenant's headline ROI with someone else's costs.
        """
        other = uuid.uuid4()
        await ensure_tenant(db, other, name="Other Tenant")
        integration = Integration(
            tenant_id=other,
            provider=IntegrationProvider.ANTHROPIC,
            display_name="Anthropic",
            status=IntegrationStatus.CONNECTED,
            is_active=True,
        )
        db.add(integration)
        await db.flush()
        db.add(
            AICostRecord(
                tenant_id=other,
                integration_id=integration.id,
                provider=CostProvider.anthropic,
                usage_date=JAN,
                cost_kind=CostKind.metered_usage,
                subject_kind=CostSubjectKind.model,
                subject_ref="claude-opus-4",
                cost_usd=Decimal("500.00"),
                cost_source=CostSource.vendor_reported,
                is_provisional=False,
                ingested_at=datetime(2026, 1, 5, tzinfo=UTC),
            )
        )
        await db.flush()

        total = await ledger_spend_usd(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert total == Decimal("0")


class TestBuildRoiEndToEnd:
    async def test_uses_real_ledger_spend_as_the_denominator(self, db) -> None:
        """The point of the slice.

        The adoption scorecard divides by an *assumed* per-seat cost. This path
        divides by what the tenant actually spent, which is why the two can
        disagree and why the endpoint is the canonical answer.
        """
        await seed_spend(db, "40.00", usage_date=date(2026, 1, 15))
        result = await build_roi(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert result.ai_spend_usd == Decimal("40.00")
        assert result.roi_multiplier is not None

    async def test_defaults_to_the_canonical_rate_when_unset(self, db) -> None:
        await seed_spend(db, "40.00", usage_date=date(2026, 1, 15))
        result = await build_roi(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert result.blended_hourly_rate_usd == DEFAULT_BLENDED_HOURLY_RATE_USD

    async def test_a_tenants_stored_rate_changes_the_headline(self, db) -> None:
        await seed_spend(db, "40.00", usage_date=date(2026, 1, 15))
        before = await build_roi(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)

        db.add(
            RoiAssumptions(
                tenant_id=TEST_TENANT_ID,
                blended_hourly_rate_usd=Decimal("150.00"),
                hours_saved_source=HoursSavedSource.adoption_pipeline,
            )
        )
        await db.flush()
        after = await build_roi(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)

        assert after.human_value_usd > before.human_value_usd
        assert after.blended_hourly_rate_usd == Decimal("150.00")

    async def test_a_tenant_with_no_spend_gets_a_null_multiplier(self, db) -> None:
        result = await build_roi(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert result.ai_spend_usd == Decimal("0.00")
        assert result.roi_multiplier is None

    async def test_the_default_path_is_flagged_illustrative(self, db) -> None:
        """The state every tenant is in today.

        Nothing yet produces `measured`, so a freshly connected customer must
        see the illustration badge rather than a number presented as theirs.
        """
        await seed_spend(db, "40.00", usage_date=date(2026, 1, 15))
        result = await build_roi(db, tenant_id=TEST_TENANT_ID, since=JAN, until=JAN_END)
        assert result.is_illustrative is True
        assert result.basis is HoursSavedBasis.sampled


class TestOneRateAcrossTheProduct:
    def test_adoption_service_uses_the_canonical_default(self) -> None:
        """The three-divergent-rates bug, pinned.

        `adoption_service` carried $75 and the AI Spend page carried $95, so an
        organisation read a different ROI depending on which page it opened.
        Both now derive from one constant.
        """
        from app.services import adoption_service

        assert adoption_service._BLENDED_HOURLY_COST == float(DEFAULT_BLENDED_HOURLY_RATE_USD)


class TestRoiEndpoints:
    """HTTP surface for the ROI model.

    The interesting cases are the boundaries rather than the happy path: who
    may change the numbers behind an organisation-wide headline, and what the
    API does with input that would produce a misleading figure.
    """

    ROI = "/api/v1/cost/roi"
    ASSUMPTIONS = "/api/v1/cost/roi/assumptions"

    async def test_roi_requires_authentication(self, client: AsyncClient) -> None:
        resp = await client.get(f"{self.ROI}?since=2026-01-01&until=2026-01-31")
        assert resp.status_code in (401, 403)

    async def test_roi_returns_the_provenance_fields(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        """The badge the UI renders is part of the contract, not a nicety."""
        resp = await client.get(
            f"{self.ROI}?since=2026-01-01&until=2026-01-31",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["basis"] in {"measured", "sampled", "manual"}
        assert isinstance(body["is_illustrative"], bool)
        assert body["basis_detail"]

    async def test_roi_multiplier_is_null_with_no_spend(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        """Serialised as JSON null, not omitted and not a number.

        A consumer that reads a missing key as 0 and a present key as a ratio
        would render "0x ROI" for a tenant that simply has no connector yet.
        """
        resp = await client.get(
            f"{self.ROI}?since=2026-01-01&until=2026-01-31",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "roi_multiplier" in body
        assert body["roi_multiplier"] is None

    async def test_a_reversed_window_is_rejected_not_computed(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        resp = await client.get(
            f"{self.ROI}?since=2026-01-31&until=2026-01-01",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 422, resp.text

    async def test_assumptions_are_readable_by_a_viewer(
        self, client: AsyncClient, viewer_token: str, seeded_principals
    ) -> None:
        """Everyone who reads the headline may read its footnote.

        Hiding the assumptions from the people shown the number would defeat
        the point of moving them out of one admin's browser.
        """
        resp = await client.get(self.ASSUMPTIONS, headers=auth_header(viewer_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_default"] is True

    async def test_a_viewer_may_not_change_them(
        self, client: AsyncClient, viewer_token: str, seeded_principals
    ) -> None:
        """Reading is for everyone; rewriting the org's ROI is not."""
        resp = await client.put(
            self.ASSUMPTIONS,
            headers=auth_header(viewer_token),
            json={
                "blended_hourly_rate_usd": "500.00",
                "hours_saved_source": "adoption_pipeline",
            },
        )
        assert resp.status_code == 403, resp.text

    async def test_an_admin_can_set_them_and_the_default_flag_clears(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        resp = await client.put(
            self.ASSUMPTIONS,
            headers=auth_header(tenant_admin_token),
            json={
                "blended_hourly_rate_usd": "120.00",
                "hours_saved_source": "manual",
                "manual_hours_saved_per_month": "300",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(str(body["blended_hourly_rate_usd"])) == Decimal("120.00")
        assert body["is_default"] is False

        follow = await client.get(self.ASSUMPTIONS, headers=auth_header(tenant_admin_token))
        assert follow.json()["is_default"] is False

    async def test_manual_source_without_hours_is_rejected_at_the_edge(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        """Caught on write rather than papered over on read.

        The service falls back safely if such a row somehow exists, but
        accepting the request would leave an admin believing they had
        configured something they had not.
        """
        resp = await client.put(
            self.ASSUMPTIONS,
            headers=auth_header(tenant_admin_token),
            json={
                "blended_hourly_rate_usd": "120.00",
                "hours_saved_source": "manual",
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_a_zero_rate_is_rejected(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        """A zero rate makes every hour worthless and the ROI identically 0."""
        resp = await client.put(
            self.ASSUMPTIONS,
            headers=auth_header(tenant_admin_token),
            json={
                "blended_hourly_rate_usd": "0",
                "hours_saved_source": "adoption_pipeline",
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_an_unknown_field_is_rejected(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        """A silently ignored typo would leave the headline unchanged."""
        resp = await client.put(
            self.ASSUMPTIONS,
            headers=auth_header(tenant_admin_token),
            json={
                "blended_hourly_rate_usd": "120.00",
                "hours_saved_source": "adoption_pipeline",
                "blended_hourly_rate": "999.00",
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_setting_the_rate_changes_the_reported_roi(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        """End to end: the stored model actually drives the headline."""
        before = await client.get(
            f"{self.ROI}?since=2026-01-01&until=2026-01-31",
            headers=auth_header(tenant_admin_token),
        )
        assert before.status_code == 200, before.text

        await client.put(
            self.ASSUMPTIONS,
            headers=auth_header(tenant_admin_token),
            json={
                "blended_hourly_rate_usd": "400.00",
                "hours_saved_source": "adoption_pipeline",
            },
        )

        after = await client.get(
            f"{self.ROI}?since=2026-01-01&until=2026-01-31",
            headers=auth_header(tenant_admin_token),
        )
        assert after.status_code == 200, after.text
        assert Decimal(str(after.json()["human_value_usd"])) > Decimal(
            str(before.json()["human_value_usd"])
        )

    async def test_the_change_is_written_to_the_audit_log(
        self, client: AsyncClient, tenant_admin_token: str, seeded_principals
    ) -> None:
        """An unexplained jump in the headline must be answerable from record."""
        from app.models.audit_log import AuditLog

        await client.put(
            self.ASSUMPTIONS,
            headers=auth_header(tenant_admin_token),
            json={
                "blended_hourly_rate_usd": "133.00",
                "hours_saved_source": "adoption_pipeline",
            },
        )

        async with TestSessionLocal() as session:
            entries = list(
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.event_type == "cost.roi_assumptions.updated"
                        )
                    )
                ).scalars()
            )
        assert entries, "no audit entry written for the assumptions change"
        assert "133.00" in (entries[-1].details or "")
