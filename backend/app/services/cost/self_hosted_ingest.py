"""Ingest self-hosted AI usage into the cost ledger — slice 2.

Slice 1 pulls billed dollars from a vendor's API. This is the other direction:
the customer's own app, running on Azure AI Foundry / AWS Bedrock / anywhere,
reports the tokens it burned and we derive the cost. There is no per-tenant
billing API to pull for those — the spend is on the customer's cloud bill,
undifferentiated by application.

Per-call in, daily-grain out. The design doc fixed the ledger's shape in slice 1
"so it does not need to be re-cut later", so this rolls calls up to the same
`(tenant, integration, usage_date, cost_kind, subject_kind, subject_ref)` row a
connector would write, with `subject_kind=model`.

**The write mode differs from every other cost path, and that is the crux.** A
pull connector re-fetches a whole day and *overwrites* the row, so re-running it
is harmless. A pushed batch carries only the calls since the last push, so it
must **accumulate** — and accumulation is not idempotent. A client that times
out and retries would silently double its own reported spend. So every accepted
batch is recorded by its client-supplied id (`AICostUsageBatch`) and a replay
short-circuits to the original answer.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_guc
from app.models.ai_cost_record import (
    AICostRecord,
    CostKind,
    CostSource,
    CostSubjectKind,
    SelfHostedCostProvider,
)
from app.models.ai_cost_usage_batch import AICostUsageBatch
from app.models.integration import Integration, IntegrationProvider, IntegrationStatus
from app.schemas.cost import SelfHostedUsageRecord
from app.services.cost.price_book import (
    derive_cost_usd,
    load_price_book,
    normalise_model,
    price_for,
)

logger = structlog.get_logger()

# Which Integration row a pushed provider hangs off. The ledger's grain key
# includes integration_id, so pushed usage needs one even though there is no
# OAuth connection behind it.
_INTEGRATION_FOR_PROVIDER: dict[SelfHostedCostProvider, IntegrationProvider] = {
    SelfHostedCostProvider.azure_ai_foundry: IntegrationProvider.AZURE_AI_FOUNDRY,
    SelfHostedCostProvider.aws_bedrock: IntegrationProvider.AWS_BEDROCK,
    SelfHostedCostProvider.self_hosted: IntegrationProvider.SELF_HOSTED_AI,
}

_DISPLAY_NAME: dict[SelfHostedCostProvider, str] = {
    SelfHostedCostProvider.azure_ai_foundry: "Azure AI Foundry (self-reported)",
    SelfHostedCostProvider.aws_bedrock: "AWS Bedrock (self-reported)",
    SelfHostedCostProvider.self_hosted: "Self-hosted models (self-reported)",
}


@dataclass
class IngestResult:
    accepted_calls: int = 0
    skipped_calls: int = 0
    rows_touched: int = 0
    cost_usd: Decimal = Decimal("0")
    unpriced_models: list[str] = field(default_factory=list)
    duplicate: bool = False


@dataclass
class _Bucket:
    """One (usage_date, model) rollup, before it meets the ledger."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")
    calls: int = 0
    priced: bool = True


def _usage_date(record: SelfHostedUsageRecord, now: datetime) -> date:
    """The UTC day the call belongs to.

    A naive client timestamp is read as UTC rather than rejected — the ledger's
    grain is a UTC day and clients in the wild send both. A missing timestamp
    means "now", which is what an app streaming its own usage means.
    """
    if record.occurred_at is None:
        return now.date()
    moment = record.occurred_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def bucket_records(
    records: list[SelfHostedUsageRecord],
    *,
    price_book,
    now: datetime,
) -> tuple[dict[tuple[date, str], _Bucket], IngestResult]:
    """Roll per-call records up to (day, model), deriving cost as we go.

    Pure: no database. A record whose model has no price is still counted and
    its tokens still recorded — only its cost contribution is zero, and the
    model is reported back as unpriced. Dropping it would understate usage;
    pricing it at zero would understate spend while looking authoritative.
    """
    buckets: dict[tuple[date, str], _Bucket] = defaultdict(_Bucket)
    result = IngestResult()
    unpriced: set[str] = set()

    for record in records:
        if record.tokens_in == 0 and record.tokens_out == 0:
            # A call that burned nothing tells the ledger nothing and would
            # create an empty row. Counted as skipped so the client can see it.
            result.skipped_calls += 1
            continue

        key = (_usage_date(record, now), normalise_model(record.model))
        bucket = buckets[key]
        bucket.tokens_in += record.tokens_in
        bucket.tokens_out += record.tokens_out
        bucket.calls += 1

        price = price_for(record.model, price_book)
        if price is None:
            bucket.priced = False
            unpriced.add(record.model.strip())
        else:
            bucket.cost_usd += derive_cost_usd(
                tokens_in=record.tokens_in,
                tokens_out=record.tokens_out,
                price=price,
            )
        result.accepted_calls += 1

    result.unpriced_models = sorted(unpriced)
    return buckets, result


async def resolve_integration(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    provider: SelfHostedCostProvider,
) -> Integration:
    """The Integration row pushed usage hangs off, created on first push.

    There is no connect flow for a self-reporting app — the customer
    instruments it and starts sending. Auto-provisioning keeps that true while
    still giving the ledger the FK it requires and the dashboard a tile to show
    status against.
    """
    integration_provider = _INTEGRATION_FOR_PROVIDER[provider]
    existing = (
        await db.execute(
            select(Integration).where(
                Integration.tenant_id == tenant_id,
                Integration.provider == integration_provider,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # A pushed batch is a live signal, so a row previously sitting at
        # COMING_SOON (the AWS/GCP placeholders) becomes really connected.
        if existing.status != IntegrationStatus.CONNECTED:
            existing.status = IntegrationStatus.CONNECTED
            existing.last_error = None
        existing.last_synced_at = datetime.now(UTC)
        return existing

    created = Integration(
        tenant_id=tenant_id,
        provider=integration_provider,
        display_name=_DISPLAY_NAME[provider],
        status=IntegrationStatus.CONNECTED,
        is_active=True,
        connected_at=datetime.now(UTC),
        last_synced_at=datetime.now(UTC),
    )
    db.add(created)
    await db.flush()
    logger.info(
        "self_hosted_cost_integration_provisioned",
        tenant_id=str(tenant_id),
        provider=provider.value,
    )
    return created


async def _find_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: str,
) -> AICostUsageBatch | None:
    """The already-recorded batch under this id, if any.

    Tenant-scoped explicitly rather than leaning on RLS, per the repo's tenancy
    rule. That matters more than usual here: `batch_id` is client-supplied, so
    a cross-tenant match would treat another customer's id as this customer's
    replay and silently discard a real batch of spend.
    """
    return (
        await db.execute(
            select(AICostUsageBatch).where(
                AICostUsageBatch.tenant_id == tenant_id,
                AICostUsageBatch.batch_id == batch_id,
            )
        )
    ).scalar_one_or_none()


def _replay_result(batch: AICostUsageBatch) -> IngestResult:
    """Reconstruct what the original push returned, from what we recorded."""
    return IngestResult(
        accepted_calls=batch.accepted_calls,
        skipped_calls=max(0, batch.call_count - batch.accepted_calls),
        rows_touched=batch.rows_touched,
        cost_usd=Decimal(str(batch.cost_usd)),
        duplicate=True,
    )


async def ingest_usage(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: SelfHostedCostProvider,
    batch_id: str,
    records: list[SelfHostedUsageRecord],
    now: datetime | None = None,
) -> IngestResult:
    """Accumulate a pushed batch into the ledger. Idempotent on ``batch_id``.

    Commits once at the end: the ledger rows and the batch marker land together,
    so a crash mid-write leaves neither and the client's retry is a clean first
    attempt rather than a partial double-count.
    """
    moment = now or datetime.now(UTC)

    integration = await resolve_integration(db, tenant_id, provider)

    # Replay guard, checked before any accumulation.
    seen = await _find_batch(db, tenant_id, batch_id)
    if seen is not None:
        logger.info(
            "self_hosted_cost_batch_replayed",
            tenant_id=str(tenant_id),
            batch_id=batch_id,
        )
        await db.commit()
        return _replay_result(seen)

    book = load_price_book(integration)
    buckets, result = bucket_records(records, price_book=book, now=moment)

    cost_provider = provider.to_cost_provider()
    today = moment.date()

    for (usage_date, model), bucket in sorted(buckets.items()):
        existing = (
            await db.execute(
                select(AICostRecord).where(
                    AICostRecord.tenant_id == tenant_id,
                    AICostRecord.integration_id == integration.id,
                    AICostRecord.usage_date == usage_date,
                    AICostRecord.cost_kind == CostKind.metered_usage,
                    AICostRecord.subject_kind == CostSubjectKind.model,
                    AICostRecord.subject_ref == model,
                )
            )
        ).scalar_one_or_none()

        # `is_provisional` tracks the day, not the batch: today's total is still
        # accumulating and will keep changing until the day closes.
        provisional = usage_date >= today

        if existing is None:
            db.add(
                AICostRecord(
                    tenant_id=tenant_id,
                    integration_id=integration.id,
                    provider=cost_provider,
                    usage_date=usage_date,
                    cost_kind=CostKind.metered_usage,
                    subject_kind=CostSubjectKind.model,
                    subject_ref=model,
                    tokens_in=bucket.tokens_in,
                    tokens_out=bucket.tokens_out,
                    seats=None,
                    quantity=Decimal(bucket.calls),
                    cost_usd=bucket.cost_usd,
                    cost_source=CostSource.derived_tokens,
                    is_provisional=provisional,
                    raw_metadata={
                        "calls": bucket.calls,
                        "priced": bucket.priced,
                        "source": "self_hosted_push",
                    },
                    ingested_at=moment,
                )
            )
        else:
            # ACCUMULATE. Overwriting here would discard every earlier push for
            # this day — the bug this whole module is shaped around.
            existing.tokens_in = (existing.tokens_in or 0) + bucket.tokens_in
            existing.tokens_out = (existing.tokens_out or 0) + bucket.tokens_out
            existing.quantity = (existing.quantity or Decimal(0)) + Decimal(bucket.calls)
            existing.cost_usd = Decimal(str(existing.cost_usd)) + bucket.cost_usd
            existing.is_provisional = provisional
            existing.ingested_at = moment
            metadata = dict(existing.raw_metadata or {})
            metadata["calls"] = int(metadata.get("calls", 0)) + bucket.calls
            # Once any part of a day's row is unpriced, the day's total is an
            # under-estimate; that must not be forgotten by a later priced push.
            metadata["priced"] = bool(metadata.get("priced", True)) and bucket.priced
            metadata["source"] = "self_hosted_push"
            existing.raw_metadata = metadata

        result.rows_touched += 1
        result.cost_usd += bucket.cost_usd

    db.add(
        AICostUsageBatch(
            tenant_id=tenant_id,
            integration_id=integration.id,
            batch_id=batch_id,
            call_count=len(records),
            accepted_calls=result.accepted_calls,
            rows_touched=result.rows_touched,
            cost_usd=result.cost_usd,
        )
    )

    try:
        await db.commit()
    except IntegrityError:
        # Two pushes of the same batch_id in flight at once: both passed the
        # replay check above, and the unique constraint caught the loser here.
        # No double count — this transaction's accumulation rolls back whole
        # with it — but the caller asked a duplicate question and deserves the
        # duplicate answer, not a 500 that reads as "your spend was not
        # recorded". This is the exact client the endpoint is built for: one
        # that retries.
        await db.rollback()
        await set_tenant_guc(db, tenant_id)
        winner = await _find_batch(db, tenant_id, batch_id)
        if winner is None:
            # The constraint that fired was not this one. Nothing to report as
            # a duplicate, so let the real error surface.
            raise
        logger.info(
            "self_hosted_cost_batch_raced",
            tenant_id=str(tenant_id),
            batch_id=batch_id,
        )
        return _replay_result(winner)

    logger.info(
        "self_hosted_cost_ingested",
        tenant_id=str(tenant_id),
        provider=provider.value,
        accepted=result.accepted_calls,
        rows=result.rows_touched,
        unpriced=len(result.unpriced_models),
    )
    return result
