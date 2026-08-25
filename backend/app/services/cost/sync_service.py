"""Cost sync orchestrator — persists connector output into the ledger.

The connectors are *pure* (fetch + normalise into ``CostRecord`` dicts);
persistence is owned here. We upsert on the 6-column daily grain key using
a portable select-then-update-or-insert pattern (see the heartbeat handler
in ``app/routers/extension.py``) so the unit tests run on SQLite — we do NOT
use Postgres ``on_conflict_do_update``.

``sync_integration`` is fault-isolating: any connector/persistence failure is
caught, recorded onto the integration (status=ERROR, last_error), committed,
and surfaced as a ``SyncResult`` — the exception never propagates out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_cost_record import AICostRecord, CostProvider, CostSubjectKind
from app.models.integration import Integration, IntegrationStatus

# ``registry`` for connector lookup; the per-vendor adapters are imported for
# their import-time side effect of registering themselves. Every cost provider
# enum value must have its connector imported here so the registry is complete.
from app.services.cost import (  # noqa: F401
    anthropic_cost,
    copilot_cost,
    cursor_cost,
    openai_cost,
    registry,
    vercel_cost,
)


@dataclass
class SyncResult:
    """Outcome of a single ``sync_integration`` call."""

    records_upserted: int
    since: date
    until: date
    status: IntegrationStatus
    error: str | None


def _cost_provider_for(integration: Integration) -> CostProvider:
    """Map an ``IntegrationProvider`` onto its ``CostProvider`` peer.

    Raises ``ValueError`` if the integration's provider is not one of the
    five cost providers.
    """
    try:
        return CostProvider(integration.provider.value.lower())
    except ValueError as exc:
        raise ValueError(
            f"Integration provider {integration.provider!r} is not a cost provider"
        ) from exc


async def sync_integration(
    db: AsyncSession,
    integration: Integration,
    since: date,
    until: date,
) -> SyncResult:
    """Fetch + persist cost records for one integration over a date window.

    On success: upserts every returned ``CostRecord`` on its grain key, marks
    the integration CONNECTED, clears ``last_error``, commits, and returns a
    ``SyncResult`` with the upsert count. On any failure: marks the integration
    ERROR with a truncated ``last_error``, commits, and returns a ``SyncResult``
    (never raises).
    """
    cost_provider = _cost_provider_for(integration)
    connector = registry.get_connector(cost_provider)

    try:
        records = await connector.fetch_cost(integration, since, until)

        upserted = 0
        now = datetime.now(UTC)
        for record in records:
            subject_kind = record["subject_kind"] or CostSubjectKind.other
            subject_ref = record["subject_ref"]

            existing = (
                await db.execute(
                    select(AICostRecord).where(
                        AICostRecord.tenant_id == integration.tenant_id,
                        AICostRecord.integration_id == integration.id,
                        AICostRecord.usage_date == record["usage_date"],
                        AICostRecord.cost_kind == record["cost_kind"],
                        AICostRecord.subject_kind == subject_kind,
                        AICostRecord.subject_ref == subject_ref,
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                db.add(
                    AICostRecord(
                        tenant_id=integration.tenant_id,
                        integration_id=integration.id,
                        provider=cost_provider,
                        usage_date=record["usage_date"],
                        cost_kind=record["cost_kind"],
                        subject_kind=subject_kind,
                        subject_ref=subject_ref,
                        tokens_in=record["tokens_in"],
                        tokens_out=record["tokens_out"],
                        seats=record["seats"],
                        quantity=record["quantity"],
                        cost_usd=record["cost_usd"],
                        cost_source=record["cost_source"],
                        is_provisional=record["is_provisional"],
                        raw_metadata=record["raw_metadata"],
                        ingested_at=now,
                    )
                )
            else:
                existing.tokens_in = record["tokens_in"]
                existing.tokens_out = record["tokens_out"]
                existing.seats = record["seats"]
                existing.quantity = record["quantity"]
                existing.cost_usd = record["cost_usd"]
                existing.cost_source = record["cost_source"]
                existing.is_provisional = record["is_provisional"]
                existing.raw_metadata = record["raw_metadata"]
                existing.ingested_at = now

            upserted += 1

        integration.status = IntegrationStatus.CONNECTED
        integration.last_error = None
        await db.commit()

        return SyncResult(
            records_upserted=upserted,
            since=since,
            until=until,
            status=IntegrationStatus.CONNECTED,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 — fault isolation is the contract.
        error = str(exc)[:500]
        # Discard any partial rows staged before the failure so a failed sync
        # never persists half its data; then record ERROR as its own commit.
        await db.rollback()
        integration.status = IntegrationStatus.ERROR
        integration.last_error = error
        await db.commit()

        return SyncResult(
            records_upserted=0,
            since=since,
            until=until,
            status=IntegrationStatus.ERROR,
            error=error,
        )
