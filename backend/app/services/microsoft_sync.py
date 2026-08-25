"""Microsoft Graph sync orchestrator — Entra ID user + group upsert.

Called by the `microsoft_sync` worker mode. For each connected
Microsoft Entra ID Integration row, this service:

  1. Decrypts the access token. If `access_token_expires_at` is past
     (or close to), refreshes via `microsoft_oauth.refresh_access_token`
     and persists the new token.
  2. Walks every user via `microsoft_graph.iter_users` — upserts a
     `DirectoryUser` row per Graph user. Email lower-cased; unique
     on `(tenant_id, integration_id, external_user_id)`.
  3. Walks every group — upserts `DirectoryGroup` rows.
  4. Sets `Integration.last_synced_at` + clears `last_error` on success.
     On failure, sets `last_error` and keeps the row at status=CONNECTED
     (transient errors should not require a re-OAuth).

The sync is *additive*. Users that disappear from Graph between
runs are flagged `is_active=False` rather than hard-deleted, because
audit history may reference them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import (
    DirectoryGroup,
    DirectoryGroupMembership,
    DirectoryUser,
)
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.services.microsoft_graph import (
    GraphAPIError,
    iter_group_members,
    iter_groups,
    iter_users,
    normalise_group,
    normalise_member,
    normalise_user,
)
from app.services.microsoft_oauth import MicrosoftOAuthError

logger = structlog.get_logger()


# ─── Public entry point ──────────────────────────────────────────────


async def sync_pending(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    batch_size: int = 50,
    stale_after: timedelta = timedelta(hours=4),
) -> int:
    """Sync every connected Entra integration whose `last_synced_at`
    is older than `stale_after` (or null).

    Returns the number of integrations processed in this batch.
    """
    cutoff = datetime.now(UTC) - stale_after
    candidate_q = (
        select(Integration)
        .where(
            Integration.provider == IntegrationProvider.MICROSOFT_ENTRA_ID,
            Integration.status == IntegrationStatus.CONNECTED,
            Integration.is_active.is_(True),
        )
        .order_by(Integration.last_synced_at.asc().nulls_first())
        .limit(batch_size)
    )
    result = await db.execute(candidate_q)
    candidates = [
        row
        for row in result.scalars().all()
        if row.last_synced_at is None or row.last_synced_at < cutoff
    ]

    processed = 0
    for integration in candidates:
        try:
            await _sync_one(db, client, integration)
            processed += 1
        except (
            GraphAPIError,
            MicrosoftOAuthError,
            httpx.HTTPError,
            ValueError,
        ) as exc:
            integration.last_error = f"{type(exc).__name__}: {exc}"[:500]
            await db.commit()
            logger.warning(
                "microsoft_sync_failed",
                integration_id=str(integration.id),
                error=str(exc),
            )
    return processed


# ─── Per-integration sync ────────────────────────────────────────────


async def _sync_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
) -> None:
    """Sync a single Entra integration's users + groups."""
    from app.services.microsoft_token_refresh import get_valid_access_token

    token = await get_valid_access_token(db, client, integration)
    now = datetime.now(UTC)

    # Users.
    user_count = 0
    seen_external_ids: set[str] = set()
    async for raw_user in iter_users(client, token=token):
        try:
            await _upsert_user(db, integration, raw_user, now)
        except Exception as exc:  # noqa: BLE001 — skip bad rows, log
            logger.warning(
                "microsoft_sync_skip_user",
                integration_id=str(integration.id),
                error=str(exc),
            )
            continue
        user_count += 1
        seen_external_ids.add(str(raw_user.get("id", "")))

    # Mark users that disappeared from Graph this run as inactive.
    deactivated = await _deactivate_missing_users(db, integration, seen_external_ids, now)

    # Groups.
    group_count = 0
    async for raw_group in iter_groups(client, token=token):
        try:
            await _upsert_group(db, integration, raw_group, now)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "microsoft_sync_skip_group",
                integration_id=str(integration.id),
                error=str(exc),
            )
            continue
        group_count += 1

    # Group memberships (user↔group) — needs the user/group rows above.
    membership_count = await _sync_group_memberships(db, integration, client, token, now)

    integration.last_synced_at = now
    integration.last_error = None
    await db.commit()

    logger.info(
        "microsoft_sync_complete",
        integration_id=str(integration.id),
        users=user_count,
        deactivated_users=deactivated,
        groups=group_count,
        memberships=membership_count,
    )


async def _upsert_user(
    db: AsyncSession,
    integration: Integration,
    raw: dict[str, Any],
    now: datetime,
) -> None:
    fields = normalise_user(raw)
    if not fields["external_user_id"]:
        return  # Skip rows missing the primary identifier.

    existing_q = select(DirectoryUser).where(
        DirectoryUser.tenant_id == integration.tenant_id,
        DirectoryUser.integration_id == integration.id,
        DirectoryUser.external_user_id == fields["external_user_id"],
    )
    existing = (await db.execute(existing_q)).scalar_one_or_none()

    if existing is None:
        db.add(
            DirectoryUser(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                external_user_id=fields["external_user_id"],
                email=fields["email"],
                display_name=fields["display_name"],
                department=fields["department"],
                job_title=fields["job_title"],
                manager_external_user_id=fields["manager_external_user_id"],
                is_active=fields["is_active"],
                last_synced_at=now,
            )
        )
    else:
        existing.email = fields["email"]
        existing.display_name = fields["display_name"]
        existing.department = fields["department"]
        existing.job_title = fields["job_title"]
        existing.manager_external_user_id = fields["manager_external_user_id"]
        existing.is_active = fields["is_active"]
        existing.last_synced_at = now


async def _deactivate_missing_users(
    db: AsyncSession,
    integration: Integration,
    seen_ids: set[str],
    now: datetime,
) -> int:
    """Flag users not seen in this sync run as inactive.

    Returns the number of rows newly deactivated.
    """
    if not seen_ids:
        return 0
    all_for_integration = await db.execute(
        select(DirectoryUser).where(
            DirectoryUser.tenant_id == integration.tenant_id,
            DirectoryUser.integration_id == integration.id,
            DirectoryUser.is_active.is_(True),
        )
    )
    deactivated = 0
    for row in all_for_integration.scalars().all():
        if row.external_user_id not in seen_ids:
            row.is_active = False
            row.last_synced_at = now
            deactivated += 1
    return deactivated


async def _upsert_group(
    db: AsyncSession,
    integration: Integration,
    raw: dict[str, Any],
    now: datetime,
) -> None:
    fields = normalise_group(raw)
    if not fields["external_group_id"]:
        return

    existing = (
        await db.execute(
            select(DirectoryGroup).where(
                DirectoryGroup.tenant_id == integration.tenant_id,
                DirectoryGroup.integration_id == integration.id,
                DirectoryGroup.external_group_id == fields["external_group_id"],
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            DirectoryGroup(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                external_group_id=fields["external_group_id"],
                display_name=fields["display_name"],
                description=fields["description"],
                group_type=fields["group_type"],
                member_count=0,
                last_synced_at=now,
            )
        )
    else:
        existing.display_name = fields["display_name"]
        existing.description = fields["description"]
        existing.group_type = fields["group_type"]
        existing.last_synced_at = now


async def _replace_group_memberships(
    db: AsyncSession,
    integration: Integration,
    group_id: uuid.UUID,
    user_ids: set[uuid.UUID],
    now: datetime,
) -> int:
    """Make the membership set for `group_id` exactly `user_ids` (idempotent).

    Adds missing rows, touches kept rows' `last_synced_at`, and prunes members
    that are no longer in the group. Returns the resulting member count.
    """
    existing = {
        m.user_id: m
        for m in (
            await db.execute(
                select(DirectoryGroupMembership).where(
                    DirectoryGroupMembership.tenant_id == integration.tenant_id,
                    DirectoryGroupMembership.integration_id == integration.id,
                    DirectoryGroupMembership.group_id == group_id,
                )
            )
        ).scalars()
    }
    for user_id in user_ids - existing.keys():
        db.add(
            DirectoryGroupMembership(
                tenant_id=integration.tenant_id,
                integration_id=integration.id,
                group_id=group_id,
                user_id=user_id,
                last_synced_at=now,
            )
        )
    for user_id in user_ids & existing.keys():
        existing[user_id].last_synced_at = now
    for user_id in existing.keys() - user_ids:
        await db.delete(existing[user_id])
    await db.flush()
    return len(user_ids)


async def _sync_group_memberships(
    db: AsyncSession,
    integration: Integration,
    client: httpx.AsyncClient,
    token: str,
    now: datetime,
) -> int:
    """Sync user↔group membership for every synced group of this integration."""
    user_id_by_external = {
        row.external_user_id: row.id
        for row in (
            await db.execute(
                select(DirectoryUser).where(
                    DirectoryUser.tenant_id == integration.tenant_id,
                    DirectoryUser.integration_id == integration.id,
                )
            )
        ).scalars()
    }
    groups = (
        (
            await db.execute(
                select(DirectoryGroup).where(
                    DirectoryGroup.tenant_id == integration.tenant_id,
                    DirectoryGroup.integration_id == integration.id,
                )
            )
        )
        .scalars()
        .all()
    )

    total = 0
    for group in groups:
        member_ids: set[uuid.UUID] = set()
        try:
            async for raw in iter_group_members(
                client, token=token, group_id=group.external_group_id
            ):
                external_id = normalise_member(raw)["external_user_id"]
                resolved = user_id_by_external.get(external_id)
                if resolved is not None:  # skip members not in our user roster
                    member_ids.add(resolved)
        except Exception as exc:  # noqa: BLE001 — one bad group never aborts the run
            logger.warning(
                "microsoft_sync_skip_group_members",
                integration_id=str(integration.id),
                group_id=str(group.id),
                error=str(exc),
            )
            continue
        total += await _replace_group_memberships(db, integration, group.id, member_ids, now)
    return total
