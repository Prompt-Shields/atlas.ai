"""Inline Slack reminder action handler.

Wires the `rmd_*` action buttons from
  - app.services.reattestation_reminder.compose_reminder_blocks
  - app.services.handbook_reminder.compose_handbook_reminder_blocks
to their DB-write effects, and returns a Block Kit confirmation
payload the Slack /interactions router replaces the original
message with.

Four verbs:

  review_done       UseCaseReview.status → REVIEWED (no drift flagged)
  review_snooze     UseCaseReview.last_reminder_sent_at += 24h
  handbook_ack      Create HandbookAcknowledgement row
  handbook_snooze   Insert HandbookReminderLog stamped now + 24h

Authorization model (defence in depth — Slack signature verifies the
request *came from Slack*, this service verifies the *clicker is
allowed* to perform the action):

  1. Resolve SlackWorkspace by team_id (must exist + be active).
  2. Resolve the clicker via users.info → email → User row in
     workspace.tenant_id. The user_id is never trusted from action_id;
     we always derive it from the verified Slack user.
  3. For review_*: clicker must be the use-case owner OR an org admin.
     For handbook_*: any authenticated tenant member may ack themselves.

On any failure we still return a Block Kit confirmation — a soft
"this didn't take" message — rather than 4xx-ing Slack. Slack retries
non-200 responses up to 3x, which would spam the user with retries.

Note: this is intentionally a fat-service module (DB + HTTP) rather
than pure helpers, because the auth + write flow is sequential
data-dependent. The Block Kit shapes are split out as pure helpers
at the bottom for unit testing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.handbook import HandbookAcknowledgement, HandbookReminderLog
from app.models.slack_workspace import SlackWorkspace
from app.models.use_case import UseCase
from app.models.use_case_review import ReviewStatus, UseCaseReview
from app.models.user import Role, User
from app.services.crypto import decrypt_token
from app.services.slack_adapter import users_info_email

logger = structlog.get_logger()


# Snooze adds this much on top of the natural REMINDER_COOLDOWN so the
# user gets a meaningful "leave me alone" effect (otherwise Snooze is
# a no-op because the cooldown already covers the next 24h).
SNOOZE_EXTRA = timedelta(hours=24)


# ─── Public entry point ──────────────────────────────────────────────


async def handle_reminder_action(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    verb: str,
    target: str,
    slack_user_id: str,
    team_id: str,
) -> dict[str, Any]:
    """Resolve, authorize, write, return the confirmation Block Kit body.

    The returned dict is the JSON body the slack /interactions endpoint
    sends back to Slack with `replace_original: true` — Slack replaces
    the original DM in place. We always return *something* (never raise
    HTTP errors) so Slack doesn't retry-spam the user.
    """
    now = datetime.now(UTC)

    # ── Resolve workspace ────────────────────────────────────────
    ws_row = await db.execute(
        select(SlackWorkspace).where(
            SlackWorkspace.team_id == team_id,
            SlackWorkspace.is_active.is_(True),
        )
    )
    workspace = ws_row.scalar_one_or_none()
    if workspace is None:
        logger.info("reminder_action_no_workspace", team_id=team_id)
        return _confirm_failure("Slack workspace not connected to atlas.")

    # ── Resolve actor via users.info → email → User row ──────────
    token = decrypt_token(workspace.bot_access_token_encrypted)
    try:
        email = await users_info_email(client, token=token, user_id=slack_user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "reminder_action_users_info_failed",
            slack_user_id=slack_user_id,
            error=str(exc),
        )
        return _confirm_failure("Couldn't verify your atlas account.")
    if not email:
        return _confirm_failure("Couldn't read your Slack email — please ack on the dashboard.")

    actor = (
        await db.execute(
            select(User).where(
                User.email == email,
                User.tenant_id == workspace.tenant_id,
                User.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if actor is None:
        return _confirm_failure(
            "Your Slack account isn't linked to atlas — please ack on the dashboard."
        )

    # ── Dispatch by verb ─────────────────────────────────────────
    if verb == "review_done":
        return await _handle_review_done(db, actor=actor, review_id=target, now=now)
    if verb == "review_snooze":
        return await _handle_review_snooze(db, actor=actor, review_id=target, now=now)
    if verb == "handbook_ack":
        return await _handle_handbook_ack(db, actor=actor, version=target, now=now)
    if verb == "handbook_snooze":
        return await _handle_handbook_snooze(
            db,
            actor=actor,
            version=target,
            tenant_id=workspace.tenant_id,
            now=now,
        )

    # Should be unreachable — parse_reminder_interaction guards verbs.
    return _confirm_failure("Unknown action.")


# ─── Re-attestation: mark reviewed ───────────────────────────────────


async def _handle_review_done(
    db: AsyncSession,
    *,
    actor: User,
    review_id: str,
    now: datetime,
) -> dict[str, Any]:
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        return _confirm_failure("Invalid review id.")

    row = (
        await db.execute(
            select(UseCaseReview, UseCase)
            .join(UseCase, UseCaseReview.use_case_id == UseCase.id)
            .where(UseCaseReview.id == rid)
        )
    ).one_or_none()
    if row is None:
        return _confirm_failure("Review not found — it may have already been resolved.")
    review, use_case = row

    if review.tenant_id != actor.tenant_id:
        return _confirm_failure("Review not found.")

    # Already done? Idempotent confirm.
    if review.status == ReviewStatus.REVIEWED:
        return _confirm_review_done(use_case.title, already=True)

    if not _can_attest_review(actor, use_case):
        return _confirm_failure("Only the use-case owner or an OrgAdmin can mark this reviewed.")

    review.status = ReviewStatus.REVIEWED
    review.reviewed_by_user_id = actor.id
    review.reviewed_at = now
    # Slack-attested → record provenance in notes for the audit trail.
    review.notes = "Marked reviewed via Slack — no material changes."
    review.drift_observed = False
    review.last_reminder_sent_at = now
    await db.commit()

    logger.info(
        "reminder_action_review_done",
        review_id=str(review.id),
        actor_id=str(actor.id),
    )
    return _confirm_review_done(use_case.title, already=False)


# ─── Re-attestation: snooze ──────────────────────────────────────────


async def _handle_review_snooze(
    db: AsyncSession,
    *,
    actor: User,
    review_id: str,
    now: datetime,
) -> dict[str, Any]:
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        return _confirm_failure("Invalid review id.")

    review = (
        await db.execute(select(UseCaseReview).where(UseCaseReview.id == rid))
    ).scalar_one_or_none()
    if review is None or review.tenant_id != actor.tenant_id:
        return _confirm_failure("Review not found.")

    # Push next-eligible reminder time out by SNOOZE_EXTRA above the
    # natural cooldown. Setting last_reminder_sent_at = now + SNOOZE_EXTRA
    # makes the dispatcher's `< now - cooldown` filter false for an
    # extra SNOOZE_EXTRA window.
    review.last_reminder_sent_at = now + SNOOZE_EXTRA
    await db.commit()

    logger.info(
        "reminder_action_review_snooze",
        review_id=str(review.id),
        actor_id=str(actor.id),
    )
    return _confirm_review_snooze()


# ─── Handbook: ack ───────────────────────────────────────────────────


async def _handle_handbook_ack(
    db: AsyncSession,
    *,
    actor: User,
    version: str,
    now: datetime,
) -> dict[str, Any]:
    # Idempotent: check for an existing ack for this version first.
    existing = (
        await db.execute(
            select(HandbookAcknowledgement).where(
                HandbookAcknowledgement.user_id == actor.id,
                HandbookAcknowledgement.version == version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _confirm_handbook_ack(version, already=True)

    if actor.tenant_id is None:
        return _confirm_failure("Your atlas account isn't tenant-scoped.")

    ack = HandbookAcknowledgement(
        tenant_id=actor.tenant_id,
        user_id=actor.id,
        version=version,
        notes="Acknowledged via Slack.",
    )
    db.add(ack)
    await db.commit()

    logger.info(
        "reminder_action_handbook_ack",
        user_id=str(actor.id),
        version=version,
    )
    return _confirm_handbook_ack(version, already=False)


# ─── Handbook: snooze ────────────────────────────────────────────────


async def _handle_handbook_snooze(
    db: AsyncSession,
    *,
    actor: User,
    version: str,
    tenant_id: uuid.UUID,
    now: datetime,
) -> dict[str, Any]:
    # The dispatcher dedups via `latest HandbookReminderLog.sent_at`.
    # Inserting a row dated now + SNOOZE_EXTRA pushes the next eligible
    # send out by SNOOZE_EXTRA beyond the natural cooldown.
    log = HandbookReminderLog(
        tenant_id=tenant_id,
        user_id=actor.id,
        version=version,
        sent_at=now + SNOOZE_EXTRA,
        slack_channel_id=None,
    )
    db.add(log)
    await db.commit()

    logger.info(
        "reminder_action_handbook_snooze",
        user_id=str(actor.id),
        version=version,
    )
    return _confirm_handbook_snooze()


# ─── Authorization helpers ───────────────────────────────────────────


def _can_attest_review(actor: User, use_case: UseCase) -> bool:
    """Owner-or-OrgAdmin gate, mirroring routers.use_case_reviews.mark_reviewed."""
    if use_case.owner_user_id == actor.id:
        return True
    role_names = {ur.role for ur in (actor.roles or [])}
    return (
        Role.ORG_ADMIN in role_names
        or Role.TENANT_ADMIN in role_names
        or Role.SUPER_ADMIN in role_names
    )


# ─── Confirmation Block Kit composers (pure) ─────────────────────────


def _confirm_review_done(use_case_title: str, *, already: bool) -> dict[str, Any]:
    headline = (
        f":white_check_mark: *{use_case_title}* already marked reviewed."
        if already
        else f":white_check_mark: *{use_case_title}* marked reviewed."
    )
    return {
        "replace_original": "true",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": headline},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "Recorded as: _no material changes_. "
                            "Open the dashboard if you need to flag drift."
                        ),
                    }
                ],
            },
        ],
    }


def _confirm_review_snooze() -> dict[str, Any]:
    return {
        "replace_original": "true",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":zzz: Snoozed for 24h — atlas will check back in tomorrow.",
                },
            },
        ],
    }


def _confirm_handbook_ack(version: str, *, already: bool) -> dict[str, Any]:
    headline = (
        f":white_check_mark: Already acknowledged handbook *v{version}*."
        if already
        else f":white_check_mark: Acknowledged handbook *v{version}*. Thank you."
    )
    return {
        "replace_original": "true",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": headline},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": ("You'll be prompted again when the handbook version changes."),
                    }
                ],
            },
        ],
    }


def _confirm_handbook_snooze() -> dict[str, Any]:
    return {
        "replace_original": "true",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":zzz: Snoozed for 24h — atlas will check back in tomorrow.",
                },
            },
        ],
    }


def _confirm_failure(message: str) -> dict[str, Any]:
    """User-facing failure block. Returned (not raised) so Slack doesn't retry."""
    return {
        "replace_original": "false",  # keep the original DM around
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":warning: {message}"},
            }
        ],
    }
