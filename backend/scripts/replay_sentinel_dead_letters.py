"""Replay CLI for the Sentinel dead-letter queue (spec §7, item 5).

The forwarder's audit guarantee is that every event lands in Sentinel or sits
visibly in the dead-letter queue. This is the tool that closes the loop: after
the cause is fixed (DCR role assignment granted, secret rotated, DCR schema
updated), it re-sends the stored payloads.

Payloads are replayed byte-identical, so every row carries the ``EventId`` it
had on the original attempt — a batch that was partly ingested before the
failure re-ingests under the same identifiers rather than as new events.

Usage:
    # What is stuck, and why
    python -m scripts.replay_sentinel_dead_letters list --tenant-slug acme

    # Re-send everything pending for one tenant
    python -m scripts.replay_sentinel_dead_letters replay --tenant-slug acme

    # Re-send one batch, or only batches that failed a particular way
    python -m scripts.replay_sentinel_dead_letters replay --tenant-slug acme --id <uuid>
    python -m scripts.replay_sentinel_dead_letters replay --tenant-slug acme --reason http_403

    # Give up on a batch that can never be sent (records why)
    python -m scripts.replay_sentinel_dead_letters discard --tenant-slug acme \
        --id <uuid> --note "DCR dropped the column; superseded by v2 stream"

``--dry-run`` on ``replay`` prints what would be sent without calling Azure.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_standalone_session, set_tenant_guc
from app.models.integration import Integration, IntegrationProvider
from app.models.sentinel_forward import SentinelDeadLetter, SentinelDeadLetterStatus
from app.models.tenant import Tenant
from app.services import sentinel_forwarder

TIMEOUT = httpx.Timeout(30.0)


@asynccontextmanager
async def tenant_scoped_session(tenant_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    """A standalone session with the RLS tenant GUC set.

    The worker has its own `worker_app.tenant_session` helper, but that module
    only exists inside the worker image (`COPY worker/app/ ./worker_app/`), so
    this script uses the backend's own `set_tenant_guc` — the same primitive
    the cost-sync cron uses in `app/routers/cost.py`.
    """
    async with get_standalone_session() as session:
        await set_tenant_guc(session, tenant_id)
        yield session


async def _resolve_tenant_id(slug: str) -> uuid.UUID:
    """Tenant lookup runs unscoped — `grc.tenants` carries no RLS policy."""
    async with get_standalone_session() as db:
        tenant_id = (
            await db.execute(select(Tenant.id).where(Tenant.slug == slug))
        ).scalar_one_or_none()
    if tenant_id is None:
        raise SystemExit(f"no tenant with slug {slug!r}")
    return tenant_id


async def _get_integration(db: AsyncSession, tenant_id: uuid.UUID) -> Integration:
    integration = (
        await db.execute(
            select(Integration).where(
                Integration.tenant_id == tenant_id,
                Integration.provider == IntegrationProvider.SENTINEL,
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        raise SystemExit("tenant has no Sentinel integration")
    return integration


async def _select_dead_letters(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    dead_letter_id: uuid.UUID | None,
    reason: str | None,
    include_replayed: bool,
) -> list[SentinelDeadLetter]:
    query = select(SentinelDeadLetter).where(SentinelDeadLetter.tenant_id == tenant_id)
    if dead_letter_id is not None:
        query = query.where(SentinelDeadLetter.id == dead_letter_id)
    elif not include_replayed:
        query = query.where(SentinelDeadLetter.status == SentinelDeadLetterStatus.PENDING)
    if reason:
        query = query.where(SentinelDeadLetter.reason == reason)
    query = query.order_by(SentinelDeadLetter.created_at)
    return list((await db.execute(query)).scalars())


async def cmd_list(args: argparse.Namespace) -> int:
    tenant_id = await _resolve_tenant_id(args.tenant_slug)
    async with tenant_scoped_session(tenant_id) as db:
        rows = await _select_dead_letters(
            db,
            tenant_id,
            dead_letter_id=None,
            reason=args.reason,
            include_replayed=args.all,
        )
        if not rows:
            print("No dead letters.")
            return 0
        print(f"{'id':38} {'status':10} {'reason':20} {'events':>6}  created")
        for row in rows:
            print(
                f"{row.id!s:38} {row.status.value:10} {row.reason:20} "
                f"{row.event_count:>6}  {row.created_at:%Y-%m-%d %H:%M:%S}"
            )
            if row.error_detail:
                print(f"{'':38} └─ {row.error_detail[:160]}")
    return 0


async def cmd_replay(args: argparse.Namespace) -> int:
    tenant_id = await _resolve_tenant_id(args.tenant_slug)
    failures = 0

    async with tenant_scoped_session(tenant_id) as db:
        integration = await _get_integration(db, tenant_id)
        if sentinel_forwarder.load_config(integration) is None:
            raise SystemExit(
                "Sentinel has no forwarder config — add the Azure Monitor "
                "coordinates before replaying"
            )

        rows = await _select_dead_letters(
            db,
            tenant_id,
            dead_letter_id=args.id,
            reason=args.reason,
            include_replayed=False,
        )
        if not rows:
            print("Nothing to replay.")
            return 0

        if args.dry_run:
            total = sum(r.event_count for r in rows)
            print(f"Would replay {len(rows)} batch(es), {total} event(s):")
            for row in rows:
                print(f"  {row.id}  {row.reason:20} {row.event_count:>6} events")
            return 0

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for row in rows:
                ok = await sentinel_forwarder.replay_dead_letter(
                    db, row, integration, client=client
                )
                if ok:
                    print(f"replayed {row.id} ({row.event_count} events)")
                else:
                    failures += 1
                    print(
                        f"FAILED   {row.id} ({row.reason}): {(row.error_detail or '')[:200]}",
                        file=sys.stderr,
                    )

    # Non-zero exit so a cron/runbook wrapper notices a partial replay.
    return 1 if failures else 0


async def cmd_discard(args: argparse.Namespace) -> int:
    tenant_id = await _resolve_tenant_id(args.tenant_slug)
    async with tenant_scoped_session(tenant_id) as db:
        row = (
            await db.execute(
                select(SentinelDeadLetter).where(
                    SentinelDeadLetter.id == args.id,
                    SentinelDeadLetter.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"no dead letter {args.id} for this tenant")
        if row.status == SentinelDeadLetterStatus.REPLAYED:
            print("Already replayed; nothing to discard.")
            return 0
        row.status = SentinelDeadLetterStatus.DISCARDED
        # The note is the audit record of why these events never reached
        # Sentinel — the queue exists so this decision is never invisible.
        row.error_detail = f"discarded: {args.note}"[:2000]
        await db.commit()
        print(f"discarded {row.id} ({row.event_count} events)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="replay_sentinel_dead_letters",
        description="Inspect and replay the Sentinel forwarder's dead-letter queue.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="show dead letters for a tenant")
    listing.add_argument("--tenant-slug", required=True)
    listing.add_argument("--reason", help="filter by reason, e.g. http_403")
    listing.add_argument("--all", action="store_true", help="include replayed/discarded batches")
    listing.set_defaults(func=cmd_list)

    replay = sub.add_parser("replay", help="re-send pending dead letters")
    replay.add_argument("--tenant-slug", required=True)
    replay.add_argument("--id", type=uuid.UUID, help="replay just this batch")
    replay.add_argument("--reason", help="only batches with this reason")
    replay.add_argument(
        "--dry-run", action="store_true", help="print what would be sent, send nothing"
    )
    replay.set_defaults(func=cmd_replay)

    discard = sub.add_parser("discard", help="mark a batch unsendable")
    discard.add_argument("--tenant-slug", required=True)
    discard.add_argument("--id", type=uuid.UUID, required=True)
    discard.add_argument("--note", required=True, help="why this batch is unsendable")
    discard.set_defaults(func=cmd_discard)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
