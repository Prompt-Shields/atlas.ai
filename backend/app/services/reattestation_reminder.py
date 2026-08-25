"""Re-attestation Slack reminder dispatcher.

Closes the operational loop on PR #54 (UseCaseReview cadence) by
DMing the use-case owner directly when a review goes OVERDUE.
Reuses the survey-bot Slack infrastructure from PR #30 — no new
Slack app, no new OAuth, no new bot token.

Flow per tick (driven by `reattestation_reminder_dispatcher` worker):

  1. Find OVERDUE reviews where:
       - last_reminder_sent_at IS NULL, OR
       - last_reminder_sent_at < now - REMINDER_COOLDOWN
  2. For each candidate:
       a. Resolve the use-case owner (User row) + email
       b. Resolve the tenant's connected SlackWorkspace (skip if none)
       c. Decrypt the workspace bot token
       d. Slack users.lookupByEmail → slack user id (skip if not found)
       e. conversations.open → DM channel id
       f. chat.postMessage with Block Kit body + link to /dashboard/reattestation
       g. Update last_reminder_sent_at
  3. On any per-row failure: log, continue. Don't poison the batch.

Deliberately minimal Block Kit
  No inline action buttons in v0.1 — those require interaction-
  handler routing (which PR #30 already supports for the survey
  bot, but each new action type needs a corresponding parser).
  v0.2 wires *Mark reviewed* + *Snooze 24h* buttons into the bot's
  interactions endpoint. Today the DM is "go to dashboard" CTA only.

Compose function is pure + unit-tested.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.slack_workspace import SlackWorkspace
from app.models.use_case import UseCase
from app.models.use_case_review import ReviewStatus, UseCaseReview
from app.models.user import User
from app.services.crypto import decrypt_token
from app.services.slack_adapter import (
    chat_post_message,
    conversations_open,
    users_lookup_by_email,
)

logger = structlog.get_logger()


# How long to wait between reminders for the same review.
REMINDER_COOLDOWN = timedelta(hours=24)


def compose_reminder_blocks(
    *,
    user_first_name: str,
    overdue_count: int,
    sample_titles: list[str],
    dashboard_url: str,
    single_review_id: str | None = None,
) -> list[dict[str, Any]]:
    """Pure-function Block Kit composer for the reminder DM.

    Args:
      user_first_name: greet by first name
      overdue_count: total overdue reviews this user owns (could be more
        than just the row triggering this reminder)
      sample_titles: up to 3 use-case titles to mention by name
      dashboard_url: deep link to /dashboard/reattestation?status=OVERDUE
      single_review_id: when the user owns exactly one overdue review,
        embedding its id here lets us render inline *No changes since
        last review* + *Snooze 24h* buttons that the interactions
        router routes via parse_reminder_interaction (rmd_review_*).
        When None (multi-review case) we render only the dashboard CTA
        — bulk inline-attestation invites accidental "yes to all" clicks
        on use cases with real drift, which is the opposite of what
        re-attestation exists for.
    """
    plural = "" if overdue_count == 1 else "s"
    is_are = "is" if overdue_count == 1 else "are"
    samples_lead = sample_titles[0] if sample_titles else "a use case"

    if overdue_count == 1:
        sample_text = f"*{sample_titles[0]}*" if sample_titles else samples_lead
    elif overdue_count <= 3 and len(sample_titles) >= 2:
        joined = ", ".join(f"*{t}*" for t in sample_titles[:overdue_count])
        sample_text = joined
    else:
        head = ", ".join(f"*{t}*" for t in sample_titles[:2])
        remainder = overdue_count - 2
        sample_text = f"{head} and {remainder} more"

    intro = (
        f"Hi {user_first_name} — you own {overdue_count} AI use "
        f"case{plural} that {is_are} overdue for re-attestation."
    )
    body = (
        f"{sample_text}. Atlas asks every 90 days: "
        "is the registered tool / model / data class / user base still "
        "accurate? Confirm or flag drift in about 90 seconds."
    )

    # Build the actions block. For the single-review case we add inline
    # *No changes since last review* + *Snooze 24h* buttons so the user
    # can attest without leaving Slack — closes the 90-second loop into
    # something closer to 5 seconds for the no-drift majority.
    action_elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": ("Review now →" if overdue_count == 1 else f"Review {overdue_count} now →"),
            },
            "style": "primary",
            "url": dashboard_url,
        }
    ]
    if overdue_count == 1 and single_review_id:
        action_elements.extend(
            [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✓ No changes",
                    },
                    "action_id": f"rmd_review_done::{single_review_id}",
                    "value": single_review_id,
                    # Slack requires `confirm` to be omitted for the
                    # silent path; we want one-click "no drift" here.
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Snooze 24h",
                    },
                    "action_id": f"rmd_review_snooze::{single_review_id}",
                    "value": single_review_id,
                },
            ]
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": intro,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": body,
            },
        },
        {
            "type": "actions",
            "elements": action_elements,
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


async def dispatch_overdue_reminders(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    batch_size: int = 50,
    dashboard_base_url: str = "https://atlas.example.com",
) -> int:
    """Find OVERDUE reviews + DM owners. Returns DMs sent."""
    now = datetime.now(UTC)
    cooldown_cutoff = now - REMINDER_COOLDOWN

    # ── Find candidate reviews ───────────────────────────────────
    candidates_q = (
        select(UseCaseReview, UseCase, User)
        .join(UseCase, UseCaseReview.use_case_id == UseCase.id)
        .join(User, UseCase.owner_user_id == User.id, isouter=True)
        .where(
            UseCaseReview.status == ReviewStatus.OVERDUE,
            or_(
                UseCaseReview.last_reminder_sent_at.is_(None),
                UseCaseReview.last_reminder_sent_at < cooldown_cutoff,
            ),
        )
        .order_by(UseCaseReview.due_at.asc())
        .limit(batch_size)
    )
    rows = (await db.execute(candidates_q)).all()

    if not rows:
        return 0

    # ── Group by owner so we send one DM per user, not per review ─
    by_owner: dict[str, list[tuple[UseCaseReview, UseCase, User]]] = {}
    for review, use_case, owner in rows:
        if owner is None:
            # Use case has no owner_user_id set — IT lead's problem,
            # not a Slack-DM problem. Skip silently.
            continue
        by_owner.setdefault(str(owner.id), []).append((review, use_case, owner))

    sent = 0
    for _owner_id, items in by_owner.items():
        try:
            n = await _dispatch_one_user(
                db,
                client=client,
                items=items,
                dashboard_base_url=dashboard_base_url,
                now=now,
            )
            sent += n
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reattestation_reminder_user_failed",
                owner_id=_owner_id,
                error=str(exc),
            )

    if sent > 0:
        logger.info("reattestation_reminder_batch", dms_sent=sent)
    return sent


async def _dispatch_one_user(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    items: list[tuple[UseCaseReview, UseCase, User]],
    dashboard_base_url: str,
    now: datetime,
) -> int:
    """DM a single owner about all their overdue reviews."""
    if not items:
        return 0
    owner = items[0][2]
    tenant_id = items[0][0].tenant_id

    # ── Resolve tenant Slack workspace ───────────────────────────
    ws = (
        await db.execute(
            select(SlackWorkspace).where(
                SlackWorkspace.tenant_id == tenant_id,
                SlackWorkspace.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if ws is None:
        logger.info(
            "reattestation_reminder_skip_no_slack",
            tenant_id=str(tenant_id),
        )
        return 0

    token = decrypt_token(ws.bot_access_token_encrypted)

    # ── Resolve Slack user by email ──────────────────────────────
    if not owner.email:
        logger.info(
            "reattestation_reminder_skip_no_email",
            user_id=str(owner.id),
        )
        return 0
    try:
        slack_user_id = await users_lookup_by_email(client, token=token, email=owner.email)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "reattestation_reminder_skip_lookup_failed",
            email=owner.email,
            error=str(exc),
        )
        return 0
    if not slack_user_id:
        return 0

    # ── Open DM channel ──────────────────────────────────────────
    try:
        channel_id = await conversations_open(client, token=token, user_id=slack_user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reattestation_reminder_open_dm_failed",
            error=str(exc),
        )
        return 0
    if not channel_id:
        return 0

    # ── Compose + post ───────────────────────────────────────────
    titles = [uc.title for _r, uc, _o in items]
    # Single-review case → embed the review id so the composer can
    # render inline action buttons. Multi-review case stays
    # dashboard-only.
    single_review_id = str(items[0][0].id) if len(items) == 1 else None
    blocks = compose_reminder_blocks(
        user_first_name=_first_name_of(owner),
        overdue_count=len(items),
        sample_titles=titles[:3],
        dashboard_url=(dashboard_base_url.rstrip("/") + "/dashboard/reattestation?status=OVERDUE"),
        single_review_id=single_review_id,
    )
    try:
        await chat_post_message(
            client,
            token=token,
            channel=channel_id,
            blocks=blocks,
            text_fallback=(
                f"You have {len(items)} overdue AI use-case review{'' if len(items) == 1 else 's'}."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reattestation_reminder_post_failed", error=str(exc))
        return 0

    # ── Stamp each review so we don't re-send within 24h ─────────
    for review, _uc, _o in items:
        review.last_reminder_sent_at = now
    await db.commit()

    return 1


def _first_name_of(user: User) -> str:
    """Best-effort first name from full_name; fallback to 'there'."""
    full = (getattr(user, "full_name", "") or "").strip()
    if full:
        return full.split()[0]
    return "there"
