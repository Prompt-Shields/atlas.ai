"""Slack-side dispatch service — sends the next question DM.

Called by:
  - The worker (`worker/app/main.py` slack_dispatcher mode) to pick
    up SurveyDelivery rows in PENDING and send the first question
    to each recipient.
  - Future: the interactions webhook in `app.routers.slack` may call
    this to send the next-question DM synchronously after an answer
    is submitted (currently it just records the answer and lets the
    worker re-poll).

Tenant-scoped throughout. Decrypts the Slack token via
`app.services.crypto.decrypt_token` only when needed, never logs it.

Rate-limited at 1 DM/sec per workspace (Slack's chat.postMessage
soft budget on Tier 3). Audiences > 60 fan out across the minute.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.slack_workspace import SlackWorkspace
from app.models.survey import (
    DeliveryStatus,
    ResponseStatus,
    SlackOptOut,
    SurveyDelivery,
    SurveyResponse,
    SurveyTemplate,
)
from app.services.crypto import decrypt_token
from app.services.slack_adapter import (
    chat_post_message,
    conversations_open,
    render_consent_block,
    render_question_blocks,
    users_lookup_by_email,
)
from app.services.survey_flow import (
    next_question as flow_next_question,
)
from app.services.survey_flow import (
    parse_questions,
)

logger = structlog.get_logger()


SLACK_RATE_LIMIT_SECONDS = 1.05  # Slack chat.postMessage Tier 3: 1/sec.


async def dispatch_pending(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    batch_size: int = 100,
) -> int:
    """Pick PENDING deliveries and send the first DM to each recipient.

    Returns the number of dispatches processed in this batch.
    """
    pending_result = await db.execute(
        select(SurveyDelivery)
        .where(SurveyDelivery.status == DeliveryStatus.PENDING)
        .order_by(SurveyDelivery.created_at.asc())
        .limit(batch_size)
    )
    pending = list(pending_result.scalars().all())
    processed = 0

    for delivery in pending:
        try:
            await _dispatch_one(db, client, delivery)
            processed += 1
        except Exception as exc:
            # Don't let one bad delivery kill the batch.
            logger.warning(
                "slack_dispatch_failed",
                delivery_id=str(delivery.id),
                error=str(exc),
            )
            delivery.status = DeliveryStatus.FAILED
            await db.commit()
    return processed


async def _dispatch_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    delivery: SurveyDelivery,
) -> None:
    """Send the first question to every recipient in a single delivery."""
    workspace_result = await db.execute(
        select(SlackWorkspace).where(
            SlackWorkspace.tenant_id == delivery.tenant_id,
            SlackWorkspace.is_active.is_(True),
        )
    )
    workspace = workspace_result.scalar_one_or_none()
    if workspace is None:
        # No workspace connected — mark FAILED so the admin sees it.
        logger.warning(
            "slack_dispatch_no_workspace",
            tenant_id=str(delivery.tenant_id),
            delivery_id=str(delivery.id),
        )
        delivery.status = DeliveryStatus.FAILED
        return

    token = decrypt_token(workspace.bot_access_token_encrypted)

    template_result = await db.execute(
        select(SurveyTemplate).where(SurveyTemplate.id == delivery.template_id)
    )
    template = template_result.scalar_one()
    questions = parse_questions(template.questions_json)
    first = flow_next_question(questions, {})
    if first.is_complete or first.question is None:
        # Template has no applicable first question — short-circuit.
        delivery.status = DeliveryStatus.SENT
        delivery.dispatched_at = datetime.now(UTC)
        return

    # Resolve recipients.
    responses_result = await db.execute(
        select(SurveyResponse).where(
            SurveyResponse.delivery_id == delivery.id,
            SurveyResponse.status == ResponseStatus.NOT_STARTED,
        )
    )
    responses = list(responses_result.scalars().all())

    delivery.status = DeliveryStatus.SENDING
    delivery.dispatched_at = datetime.now(UTC)
    await db.commit()

    # Pre-fetch opt-outs once.
    opt_out_rows = await db.execute(
        select(SlackOptOut.slack_user_id).where(SlackOptOut.tenant_id == delivery.tenant_id)
    )
    opted_out: set[str] = {row for (row,) in opt_out_rows.all()}

    # Convert flow_next_question's Question to a dict for the adapter.
    question_dict = {
        "id": first.question.id,
        "prompt": first.question.prompt,
        "type": first.question.type,
        "options": list(first.question.options),
        "optional": first.question.optional,
    }
    total_applicable = sum(1 for _ in questions)  # rough upper bound
    tenant_name = "your organisation"  # M3 Tenant.name lookup in v0.2

    for response in responses:
        await _send_one_dm(
            db=db,
            client=client,
            workspace=workspace,
            token=token,
            response=response,
            question_dict=question_dict,
            tenant_name=tenant_name,
            opted_out=opted_out,
            total_applicable=total_applicable,
        )
        # Slack rate limit.
        await asyncio.sleep(SLACK_RATE_LIMIT_SECONDS)

    # All recipient DMs attempted — flip to SENT.
    delivery.status = DeliveryStatus.SENT
    await db.commit()


async def _send_one_dm(
    *,
    db: AsyncSession,
    client: httpx.AsyncClient,
    workspace: SlackWorkspace,
    token: str,
    response: SurveyResponse,
    question_dict: dict[str, Any],
    tenant_name: str,
    opted_out: set[str],
    total_applicable: int,
) -> None:
    """Resolve user → open DM → post Q1 → record ts. Errors flip status."""
    # Resolve Slack user id.
    slack_user_id = response.slack_user_id
    if slack_user_id is None and response.recipient_email:
        slack_user_id = await users_lookup_by_email(client, token, response.recipient_email)
        if slack_user_id:
            response.slack_user_id = slack_user_id

    if not slack_user_id:
        logger.info(
            "slack_dispatch_unresolved",
            response_id=str(response.id),
            email=response.recipient_email,
        )
        response.status = ResponseStatus.EXPIRED
        return

    if slack_user_id in opted_out:
        response.status = ResponseStatus.OPTED_OUT
        return

    # Open DM channel.
    channel_id = await conversations_open(client, token, slack_user_id)
    if not channel_id:
        logger.warning("slack_dispatch_open_dm_failed", user=slack_user_id)
        return

    # Build the message: consent header + Q1 blocks.
    blocks = [
        render_consent_block(tenant_name),
        *render_question_blocks(
            question=question_dict,
            response_id=str(response.id),
            answered=0,
            total=total_applicable,
        ),
    ]
    ts = await chat_post_message(client, token, channel_id, blocks)

    response.slack_channel_id = channel_id
    response.slack_message_ts = ts
    response.status = ResponseStatus.IN_PROGRESS
    response.started_at = datetime.now(UTC)
    await db.commit()
