"""Handbook acknowledgement Slack reminder dispatcher.

Symmetric to `reattestation_reminder.py` (PR #56), but for the
handbook acknowledgement flow (PR #52). Dashboard banners (PR #55)
catch users who open atlas; this service catches the rest by DMing
them through their tenant's Slack workspace.

Flow per tick (driven by `handbook_reminder_dispatcher` worker):

  1. Find users in connected-Slack tenants who:
       a. Have not acknowledged CURRENT_HANDBOOK_VERSION
       b. Were created at least INITIAL_GRACE_DAYS ago (don't nudge
          on day one)
       c. Have no HandbookReminderLog within the past 24h
       d. Have an email address (required for users.lookupByEmail)
  2. For each candidate:
       a. Resolve tenant SlackWorkspace + decrypt bot token
       b. users.lookupByEmail → conversations.open → chat.postMessage
       c. Log to HandbookReminderLog on success
  3. Per-user failures logged, batch continues.

Compose function is pure + unit-tested.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.handbook import HandbookAcknowledgement, HandbookReminderLog
from app.models.slack_workspace import SlackWorkspace
from app.models.user import User
from app.routers.handbook import (
    CURRENT_HANDBOOK_VERSION,  # noqa: F401  retained for back-compat callers
    resolve_handbook_version,
)
from app.services.crypto import decrypt_token
from app.services.slack_adapter import (
    chat_post_message,
    conversations_open,
    users_lookup_by_email,
)

logger = structlog.get_logger()


# Don't nudge brand-new users — give them time to settle in. After
# this many days, start sending reminders if they still haven't ack'd.
INITIAL_GRACE_DAYS = 3

# 24h cooldown between reminders to the same user for the same version.
REMINDER_COOLDOWN = timedelta(hours=24)


# ─── Pure composer ───────────────────────────────────────────────────


def compose_handbook_reminder_blocks(
    *,
    user_first_name: str,
    handbook_version: str,
    handbook_url: str,
    days_since_signup: int,
) -> list[dict[str, Any]]:
    """Block Kit composer for the handbook reminder DM.

    Args:
      user_first_name: greet by first name (best-effort)
      handbook_version: current version string (e.g. "1.0")
      handbook_url: full URL to /dashboard/handbook
      days_since_signup: tunes copy ("welcome" tone vs "still pending")

    Returns: list of Block Kit blocks ready for chat.postMessage.
    """
    is_first_week = days_since_signup <= 7
    tone_intro = "Welcome to atlas." if is_first_week else "Quick reminder."

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{tone_intro} Hi {user_first_name} — please read the "
                    f"*AI governance handbook* (v{handbook_version}). "
                    "5-minute read, one-time acknowledgement."
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "The handbook explains what atlas sees about you "
                    "(SHA-256 hashes only — never your prompts), what's "
                    "expected of you, your rights as a user, and how to "
                    "flag concerns."
                ),
            },
        },
        {
            "type": "actions",
            # Three buttons: primary CTA → dashboard; inline "I've read
            # it" creates a HandbookAcknowledgement without leaving
            # Slack; "Snooze 24h" defers the next reminder cycle.
            # Routed in /slack/interactions by rmd_handbook_* action_id
            # prefix (see services.slack_adapter.parse_reminder_interaction).
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Read the handbook →",
                    },
                    "style": "primary",
                    "url": handbook_url,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✓ I've read it",
                    },
                    "action_id": f"rmd_handbook_ack::{handbook_version}",
                    "value": handbook_version,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Snooze 24h",
                    },
                    "action_id": f"rmd_handbook_snooze::{handbook_version}",
                    "value": handbook_version,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (":lock: Atlas — AI governance · Reply STOP to disable atlas DMs."),
                }
            ],
        },
    ]


# ─── Public entry point ──────────────────────────────────────────────


async def dispatch_handbook_reminders(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    batch_size: int = 100,
    dashboard_base_url: str = "https://atlas.example.com",
) -> int:
    """Find unacknowledged users + DM them. Returns DMs sent.

    Iterates tenants because each tenant can have its own
    TenantHandbookOverride.version — the "did this user ack?" check
    must be against the *effective* version for the user's tenant,
    not a global constant.
    """
    now = datetime.now(UTC)

    # Distinct tenants with at least one active user that could be a
    # candidate. The actual candidate filter happens per-tenant once
    # we know which handbook version applies.
    tenant_rows = (
        (
            await db.execute(
                select(User.tenant_id)
                .distinct()
                .where(
                    User.is_active.is_(True),
                    User.tenant_id.is_not(None),
                    User.email.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    sent = 0
    remaining = batch_size
    for tenant_id in tenant_rows:
        if remaining <= 0:
            break
        try:
            n = await _dispatch_one_tenant(
                db,
                client=client,
                tenant_id=tenant_id,
                dashboard_base_url=dashboard_base_url,
                now=now,
                max_to_send=remaining,
            )
            sent += n
            remaining -= n
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "handbook_reminder_tenant_failed",
                tenant_id=str(tenant_id),
                error=str(exc),
            )

    if sent > 0:
        logger.info("handbook_reminder_batch", dms_sent=sent)
    return sent


async def _dispatch_one_tenant(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    tenant_id,
    dashboard_base_url: str,
    now: datetime,
    max_to_send: int,
) -> int:
    """DM unacknowledged users in a single tenant against THIS tenant's
    effective handbook version.

    Lookup order:
      1. SlackWorkspace for the tenant (skip if none).
      2. Effective handbook version (override or stock).
      3. Candidate users: active, with email, past grace, not acked
         for `effective_version`, no recent reminder for `effective_version`.
    """
    if max_to_send <= 0:
        return 0

    ws = (
        await db.execute(
            select(SlackWorkspace).where(
                SlackWorkspace.tenant_id == tenant_id,
                SlackWorkspace.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if ws is None:
        # No Slack connected — nothing to do for this tenant.
        return 0

    effective_version = await resolve_handbook_version(db, tenant_id)
    grace_cutoff = now - timedelta(days=INITIAL_GRACE_DAYS)
    cooldown_cutoff = now - REMINDER_COOLDOWN

    acked_user_ids_sq = (
        select(HandbookAcknowledgement.user_id)
        .where(
            HandbookAcknowledgement.tenant_id == tenant_id,
            HandbookAcknowledgement.version == effective_version,
        )
        .subquery()
    )

    latest_reminder_sq = (
        select(
            HandbookReminderLog.user_id,
            func.max(HandbookReminderLog.sent_at).label("max_sent_at"),
        )
        .where(
            HandbookReminderLog.tenant_id == tenant_id,
            HandbookReminderLog.version == effective_version,
        )
        .group_by(HandbookReminderLog.user_id)
        .subquery()
    )

    candidates_q = (
        select(User)
        .outerjoin(
            latest_reminder_sq,
            User.id == latest_reminder_sq.c.user_id,
        )
        .where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            User.email.is_not(None),
            User.created_at <= grace_cutoff,
            not_(User.id.in_(select(acked_user_ids_sq.c.user_id))),
            (latest_reminder_sq.c.max_sent_at.is_(None))
            | (latest_reminder_sq.c.max_sent_at < cooldown_cutoff),
        )
        .order_by(User.created_at.asc())
        .limit(max_to_send)
    )
    tenant_users = list((await db.execute(candidates_q)).scalars().all())
    if not tenant_users:
        return 0

    token = decrypt_token(ws.bot_access_token_encrypted)
    handbook_url = dashboard_base_url.rstrip("/") + "/dashboard/handbook"

    sent = 0
    for user in tenant_users:
        try:
            if await _dm_one_user(
                db,
                client=client,
                token=token,
                user=user,
                handbook_url=handbook_url,
                handbook_version=effective_version,
                now=now,
            ):
                sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "handbook_reminder_user_failed",
                user_id=str(user.id),
                error=str(exc),
            )
    return sent


async def _dm_one_user(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    token: str,
    user: User,
    handbook_url: str,
    handbook_version: str,
    now: datetime,
) -> bool:
    """Send the reminder DM. Returns True if sent + logged."""
    if not user.email:
        return False
    try:
        slack_user_id = await users_lookup_by_email(client, token=token, email=user.email)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "handbook_reminder_skip_lookup_failed",
            email=user.email,
            error=str(exc),
        )
        return False
    if not slack_user_id:
        return False

    try:
        channel_id = await conversations_open(client, token=token, user_id=slack_user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "handbook_reminder_open_dm_failed",
            user_id=str(user.id),
            error=str(exc),
        )
        return False
    if not channel_id:
        return False

    user_created_aware = (
        user.created_at
        if user.created_at.tzinfo is not None
        else user.created_at.replace(tzinfo=UTC)
    )
    days_since_signup = max(0, (now - user_created_aware).days)

    blocks = compose_handbook_reminder_blocks(
        user_first_name=_first_name_of(user),
        handbook_version=handbook_version,
        handbook_url=handbook_url,
        days_since_signup=days_since_signup,
    )
    try:
        await chat_post_message(
            client,
            token=token,
            channel=channel_id,
            blocks=blocks,
            text_fallback=("Please read the AI governance handbook on atlas."),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "handbook_reminder_post_failed",
            user_id=str(user.id),
            error=str(exc),
        )
        return False

    # Log the reminder. assert tenant_id is not None — guarded at
    # query time but mypy doesn't know.
    if user.tenant_id is None:
        return False
    db.add(
        HandbookReminderLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            version=handbook_version,
            sent_at=now,
            slack_channel_id=channel_id,
        )
    )
    await db.commit()
    return True


def _first_name_of(user: User) -> str:
    """Best-effort first name from full_name; fallback to 'there'."""
    full = (getattr(user, "full_name", "") or "").strip()
    if full:
        return full.split()[0]
    return "there"
