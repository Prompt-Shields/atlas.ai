"""Self-hosted usage ingestion — cost-ledger slice 2.

The property that matters most here is the one that differs from every other
cost path: pushed usage **accumulates** into a day's ledger row rather than
overwriting it. Get that wrong in one direction and a customer's reported spend
silently halves; get it wrong in the other and a retried batch doubles it. Both
produce a plausible-looking number, which is why they are pinned explicitly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.ai_cost_record import (
    AICostRecord,
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
    SelfHostedCostProvider,
)
from app.models.ai_cost_usage_batch import AICostUsageBatch
from app.models.integration import Integration, IntegrationProvider, IntegrationStatus
from app.schemas.cost import SelfHostedUsageRecord
from app.services.cost import self_hosted_ingest
from app.services.cost.price_book import (
    DEFAULT_PRICE_BOOK,
    derive_cost_usd,
    load_price_book,
    normalise_model,
    price_for,
)
from app.services.cost.self_hosted_ingest import (
    bucket_records,
    ingest_usage,
    resolve_integration,
)
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def rec(**overrides) -> SelfHostedUsageRecord:
    defaults: dict = {
        "model": "gpt-4o",
        "tokens_in": 1000,
        "tokens_out": 500,
        "occurred_at": NOW,
    }
    defaults.update(overrides)
    return SelfHostedUsageRecord(**defaults)


@pytest_asyncio.fixture
async def db(setup_database):
    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID, name="Test Tenant")
        yield session


async def ledger_rows(db) -> list[AICostRecord]:
    return list(
        (await db.execute(select(AICostRecord).order_by(AICostRecord.subject_ref))).scalars()
    )


# ─── Price book ──────────────────────────────────────────────────────


class TestPriceBook:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("gpt-4o", "gpt-4o"),
            ("GPT-4o", "gpt-4o"),
            ("  gpt-4o  ", "gpt-4o"),
            # Azure deployment names carry a date suffix.
            ("gpt-4o-2024-08-06", "gpt-4o"),
            # Bedrock model ids are vendor.model-version:revision.
            ("anthropic.claude-sonnet-4-v1:0", "claude-sonnet-4"),
        ],
    )
    def test_normalises_vendor_deployment_names(self, raw, expected) -> None:
        assert normalise_model(raw) == expected

    def test_every_built_in_key_normalises_to_itself(self) -> None:
        """The invariant that catches an over-eager normaliser.

        Regression: an earlier version stripped any trailing number as a
        "version", so `claude-sonnet-4` — a key in this very book — normalised
        to `claude-sonnet`, missed, and came back unpriced. Three of nine
        built-in models silently priced at nothing, which under-reports exactly
        the spend a cost tool exists to measure.
        """
        unreachable = [k for k in DEFAULT_PRICE_BOOK if normalise_model(k) != k]
        assert not unreachable, f"price-book keys unreachable via lookup: {unreachable}"

    def test_every_built_in_model_actually_prices(self) -> None:
        for key in DEFAULT_PRICE_BOOK:
            assert price_for(key, DEFAULT_PRICE_BOOK) is not None, key

    def test_a_bare_trailing_number_is_model_identity_not_a_version(self) -> None:
        assert normalise_model("claude-sonnet-4") == "claude-sonnet-4"
        assert normalise_model("llama-3-8b") == "llama-3-8b"

    def test_prices_a_known_model(self) -> None:
        price = price_for("gpt-4o", DEFAULT_PRICE_BOOK)
        assert price is not None
        assert price.input_per_mtok == Decimal("2.50")

    def test_unknown_model_is_none_not_zero(self) -> None:
        # Zero would render as "$0.00" on a spend dashboard — indistinguishable
        # from a free model, and an under-report of the real bill.
        assert price_for("some-bespoke-finetune", DEFAULT_PRICE_BOOK) is None

    def test_derives_cost_from_tokens(self) -> None:
        price = price_for("gpt-4o", DEFAULT_PRICE_BOOK)
        assert price is not None
        # 1M in @ $2.50 + 1M out @ $10.00
        cost = derive_cost_usd(tokens_in=1_000_000, tokens_out=1_000_000, price=price)
        assert cost == Decimal("12.50")

    def test_keeps_sub_cent_precision(self) -> None:
        price = price_for("gpt-4o-mini", DEFAULT_PRICE_BOOK)
        assert price is not None
        cost = derive_cost_usd(tokens_in=1000, tokens_out=0, price=price)
        # 1000 tokens @ $0.15/1M = $0.00015 — rounding here would lose it.
        assert cost == Decimal("0.00015")

    def test_integration_override_wins_over_the_default(self) -> None:
        integration = Integration(
            tenant_id=TEST_TENANT_ID,
            provider=IntegrationProvider.AZURE_AI_FOUNDRY,
            display_name="x",
            config_json=(
                '{"price_book": {"gpt-4o": {"input_per_mtok": "1.00", "output_per_mtok": "2.00"}}}'
            ),
        )
        book = load_price_book(integration)
        price = price_for("gpt-4o", book)
        assert price is not None
        assert price.input_per_mtok == Decimal("1.00")

    def test_a_malformed_override_entry_is_skipped_not_fatal(self) -> None:
        # One bad price must not stop a customer reporting spend at all.
        integration = Integration(
            tenant_id=TEST_TENANT_ID,
            provider=IntegrationProvider.AZURE_AI_FOUNDRY,
            display_name="x",
            config_json='{"price_book": {"gpt-4o": "not-a-price"}}',
        )
        book = load_price_book(integration)
        assert price_for("gpt-4o", book) == DEFAULT_PRICE_BOOK["gpt-4o"]

    def test_unparseable_config_falls_back_to_defaults(self) -> None:
        integration = Integration(
            tenant_id=TEST_TENANT_ID,
            provider=IntegrationProvider.AZURE_AI_FOUNDRY,
            display_name="x",
            config_json="{not json",
        )
        assert load_price_book(integration) == DEFAULT_PRICE_BOOK


# ─── Bucketing (pure) ────────────────────────────────────────────────


class TestBucketRecords:
    def test_groups_by_day_and_model(self) -> None:
        records = [
            rec(model="gpt-4o"),
            rec(model="gpt-4o"),
            rec(model="claude-sonnet-4"),
            rec(model="gpt-4o", occurred_at=NOW - timedelta(days=1)),
        ]
        buckets, _ = bucket_records(records, price_book=DEFAULT_PRICE_BOOK, now=NOW)
        assert len(buckets) == 3
        assert buckets[(NOW.date(), "gpt-4o")].calls == 2
        assert buckets[(NOW.date(), "gpt-4o")].tokens_in == 2000

    def test_a_zero_token_call_is_skipped(self) -> None:
        # It would create an empty ledger row that says nothing.
        _, result = bucket_records(
            [rec(tokens_in=0, tokens_out=0)], price_book=DEFAULT_PRICE_BOOK, now=NOW
        )
        assert result.accepted_calls == 0
        assert result.skipped_calls == 1

    def test_unpriced_model_is_counted_and_reported(self) -> None:
        buckets, result = bucket_records(
            [rec(model="bespoke-finetune")], price_book=DEFAULT_PRICE_BOOK, now=NOW
        )
        assert result.accepted_calls == 1
        assert result.unpriced_models == ["bespoke-finetune"]
        bucket = buckets[(NOW.date(), "bespoke-finetune")]
        # Tokens recorded, cost zero, and flagged so the row knows it is partial.
        assert bucket.tokens_in == 1000
        assert bucket.cost_usd == Decimal("0")
        assert bucket.priced is False

    def test_naive_timestamps_are_read_as_utc(self) -> None:
        naive = datetime(2026, 8, 30, 23, 30)
        buckets, _ = bucket_records(
            [rec(occurred_at=naive)], price_book=DEFAULT_PRICE_BOOK, now=NOW
        )
        assert (naive.date(), "gpt-4o") in buckets

    def test_a_missing_timestamp_means_now(self) -> None:
        buckets, _ = bucket_records([rec(occurred_at=None)], price_book=DEFAULT_PRICE_BOOK, now=NOW)
        assert (NOW.date(), "gpt-4o") in buckets


# ─── Ingestion ───────────────────────────────────────────────────────


class TestIngestUsage:
    async def test_writes_a_derived_ledger_row(self, db) -> None:
        result = await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.azure_ai_foundry,
            batch_id="b1",
            records=[rec()],
            now=NOW,
        )
        assert result.accepted_calls == 1
        assert result.rows_touched == 1

        rows = await ledger_rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row.provider == CostProvider.azure_ai_foundry
        assert row.cost_kind == CostKind.metered_usage
        assert row.subject_kind == CostSubjectKind.model
        assert row.subject_ref == "gpt-4o"
        # Never vendor_reported: these dollars are ours to estimate, not billed.
        assert row.cost_source == CostSource.derived_tokens

    async def test_a_second_batch_accumulates_rather_than_overwriting(self, db) -> None:
        """The central property of this slice.

        Overwriting would discard the first push entirely — the day's total
        would silently become whatever the last batch happened to contain.
        """
        for batch_id in ("b1", "b2"):
            await ingest_usage(
                db,
                tenant_id=TEST_TENANT_ID,
                provider=SelfHostedCostProvider.azure_ai_foundry,
                batch_id=batch_id,
                records=[rec(tokens_in=1000, tokens_out=500)],
                now=NOW,
            )

        rows = await ledger_rows(db)
        assert len(rows) == 1
        assert rows[0].tokens_in == 2000
        assert rows[0].tokens_out == 1000
        assert rows[0].quantity == Decimal(2)

    async def test_replaying_a_batch_id_does_not_double_count(self, db) -> None:
        first = await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.azure_ai_foundry,
            batch_id="same",
            records=[rec()],
            now=NOW,
        )
        replay = await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.azure_ai_foundry,
            batch_id="same",
            records=[rec()],
            now=NOW,
        )

        assert replay.duplicate is True
        assert replay.cost_usd == first.cost_usd
        rows = await ledger_rows(db)
        assert len(rows) == 1
        assert rows[0].tokens_in == 1000  # not 2000

    async def test_separate_days_get_separate_rows(self, db) -> None:
        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.azure_ai_foundry,
            batch_id="b1",
            records=[rec(), rec(occurred_at=NOW - timedelta(days=2))],
            now=NOW,
        )
        rows = await ledger_rows(db)
        assert len({r.usage_date for r in rows}) == 2

    async def test_todays_row_is_provisional_and_a_past_day_is_not(self, db) -> None:
        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.azure_ai_foundry,
            batch_id="b1",
            records=[rec(), rec(model="claude-sonnet-4", occurred_at=NOW - timedelta(days=3))],
            now=NOW,
        )
        by_date = {r.usage_date: r for r in await ledger_rows(db)}
        assert by_date[NOW.date()].is_provisional is True
        assert by_date[(NOW - timedelta(days=3)).date()].is_provisional is False

    async def test_provisions_the_integration_on_first_push(self, db) -> None:
        # There is no connect flow for a self-reporting app.
        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.aws_bedrock,
            batch_id="b1",
            records=[rec(model="claude-sonnet-4")],
            now=NOW,
        )
        integration = (
            await db.execute(
                select(Integration).where(Integration.provider == IntegrationProvider.AWS_BEDROCK)
            )
        ).scalar_one()
        assert integration.status == IntegrationStatus.CONNECTED

    async def test_reuses_the_integration_across_pushes(self, db) -> None:
        for batch_id in ("b1", "b2"):
            await ingest_usage(
                db,
                tenant_id=TEST_TENANT_ID,
                provider=SelfHostedCostProvider.aws_bedrock,
                batch_id=batch_id,
                records=[rec(model="claude-sonnet-4")],
                now=NOW,
            )
        integrations = (
            (
                await db.execute(
                    select(Integration).where(
                        Integration.provider == IntegrationProvider.AWS_BEDROCK
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(integrations) == 1

    async def test_a_coming_soon_placeholder_becomes_connected(self, db) -> None:
        # AWS_BEDROCK ships as a COMING_SOON tile; a real push makes it real.
        db.add(
            Integration(
                tenant_id=TEST_TENANT_ID,
                provider=IntegrationProvider.AWS_BEDROCK,
                display_name="AWS Bedrock",
                status=IntegrationStatus.COMING_SOON,
            )
        )
        await db.commit()

        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.aws_bedrock,
            batch_id="b1",
            records=[rec(model="claude-sonnet-4")],
            now=NOW,
        )
        integration = (
            await db.execute(
                select(Integration).where(Integration.provider == IntegrationProvider.AWS_BEDROCK)
            )
        ).scalar_one()
        assert integration.status == IntegrationStatus.CONNECTED

    async def test_records_the_batch_for_replay_detection(self, db) -> None:
        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.self_hosted,
            batch_id="b-audit",
            records=[rec(), rec(tokens_in=0, tokens_out=0)],
            now=NOW,
        )
        batch = (
            await db.execute(select(AICostUsageBatch).where(AICostUsageBatch.batch_id == "b-audit"))
        ).scalar_one()
        assert batch.call_count == 2
        assert batch.accepted_calls == 1  # the zero-token call was skipped

    async def test_an_unpriced_day_stays_flagged_after_a_priced_push(self, db) -> None:
        # Once part of a day is unpriced the day's total is an under-estimate;
        # a later priced batch must not erase that.
        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.self_hosted,
            batch_id="b1",
            records=[rec(model="bespoke-finetune")],
            now=NOW,
        )
        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.self_hosted,
            batch_id="b2",
            records=[rec(model="bespoke-finetune")],
            now=NOW,
        )
        row = (await ledger_rows(db))[0]
        assert row.raw_metadata["priced"] is False
        assert row.raw_metadata["calls"] == 2

    async def test_two_tenants_do_not_share_a_batch_id(self, db) -> None:
        other_tenant = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
        # Same session: SQLite locks if a second one is opened while this is live.
        await ensure_tenant(db, other_tenant, name="Other")

        await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.self_hosted,
            batch_id="shared-id",
            records=[rec()],
            now=NOW,
        )
        result = await ingest_usage(
            db,
            tenant_id=other_tenant,
            provider=SelfHostedCostProvider.self_hosted,
            batch_id="shared-id",
            records=[rec()],
            now=NOW,
        )
        # The other tenant's push is genuinely new, not a replay of ours.
        assert result.duplicate is False


class TestConcurrentDuplicateBatch:
    """Two pushes of one batch_id in flight at once.

    Both pass the replay check before either commits, so the unique constraint
    is what actually separates them. The loser must come back as a duplicate,
    not as a server error: this endpoint exists for clients that retry, and a
    500 on a batch that *was* recorded reads as "your spend was lost" and earns
    another retry.

    The race is reproduced by blinding the replay lookup once, which is exactly
    what the real window is — the winner commits between the loser's SELECT and
    its INSERT.
    """

    @staticmethod
    async def _seed_winner(db, batch_id: str, cost: str) -> None:
        """A committed batch marker, as the winning request would leave."""
        integration = await resolve_integration(
            db, TEST_TENANT_ID, SelfHostedCostProvider.azure_ai_foundry
        )
        await db.flush()
        db.add(
            AICostUsageBatch(
                tenant_id=TEST_TENANT_ID,
                integration_id=integration.id,
                batch_id=batch_id,
                call_count=1,
                accepted_calls=1,
                rows_touched=1,
                cost_usd=Decimal(cost),
            )
        )
        await db.commit()

    @staticmethod
    def _blind_first_lookup(monkeypatch) -> None:
        """Make the replay check miss once, then behave normally."""
        real = self_hosted_ingest._find_batch
        calls = {"n": 0}

        async def blinded(db, tenant_id, batch_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return await real(db, tenant_id, batch_id)

        monkeypatch.setattr(self_hosted_ingest, "_find_batch", blinded)

    async def test_loser_reports_duplicate_rather_than_erroring(self, db, monkeypatch) -> None:
        await self._seed_winner(db, "raced", "0.500000")
        self._blind_first_lookup(monkeypatch)

        result = await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.azure_ai_foundry,
            batch_id="raced",
            records=[rec(tokens_in=1000, tokens_out=500)],
            now=NOW,
        )

        # The winner's numbers, not the loser's own.
        assert result.duplicate is True
        assert result.accepted_calls == 1
        assert result.cost_usd == Decimal("0.5")

    async def test_the_losing_accumulation_is_rolled_back(self, db, monkeypatch) -> None:
        """No double count survives the race.

        The loser's ledger writes and its batch marker share one transaction,
        so the constraint violation rolls both back together. Were they
        committed separately, the tokens would land twice with only one marker
        recording it — invisible, and permanent.
        """
        await self._seed_winner(db, "raced-2", "0.250000")

        before = {
            (r.usage_date, r.subject_ref): (r.tokens_in, r.tokens_out)
            for r in await ledger_rows(db)
        }
        self._blind_first_lookup(monkeypatch)

        result = await ingest_usage(
            db,
            tenant_id=TEST_TENANT_ID,
            provider=SelfHostedCostProvider.azure_ai_foundry,
            batch_id="raced-2",
            records=[rec(tokens_in=9999, tokens_out=9999)],
            now=NOW,
        )
        assert result.duplicate is True

        after = {
            (r.usage_date, r.subject_ref): (r.tokens_in, r.tokens_out)
            for r in await ledger_rows(db)
        }
        assert after == before, "the losing transaction left tokens behind"

    async def test_an_unrelated_integrity_error_still_surfaces(self, db, monkeypatch) -> None:
        """Only a duplicate batch_id is swallowed.

        Reporting every IntegrityError as a successful duplicate would hide
        real write failures behind a 200, which is the worst possible answer
        for a ledger.
        """
        self._blind_first_lookup(monkeypatch)

        async def boom(*_args, **_kwargs):
            raise IntegrityError("INSERT ...", {}, Exception("some other constraint"))

        monkeypatch.setattr(db, "commit", boom)

        with pytest.raises(IntegrityError):
            await ingest_usage(
                db,
                tenant_id=TEST_TENANT_ID,
                provider=SelfHostedCostProvider.azure_ai_foundry,
                batch_id="never-recorded",
                records=[rec()],
                now=NOW,
            )


class TestProviderSeparation:
    def test_push_providers_are_excluded_from_the_pull_sweep(self) -> None:
        """A pushed integration has no connector to pull from.

        Left in the sweep the cron would try to sync it every run, fail, and
        flip the status to ERROR — undoing the CONNECTED the push just set, on
        a loop, for an integration that is working perfectly.
        """
        from app.routers.cost import _COST_PROVIDERS

        pull = {p.value for p in _COST_PROVIDERS}
        assert "AWS_BEDROCK" not in pull
        assert "AZURE_AI_FOUNDRY" not in pull
        assert "ANTHROPIC" in pull

    def test_every_push_provider_maps_onto_a_cost_provider(self) -> None:
        for provider in SelfHostedCostProvider:
            assert provider.to_cost_provider().value == provider.value

    def test_pull_providers_are_not_pushable(self) -> None:
        # The push endpoint must not accept `anthropic`: that spend already
        # arrives from the billing API, and a push would double-count it.
        pushable = {p.value for p in SelfHostedCostProvider}
        assert "anthropic" not in pushable
        assert "openai" not in pushable
