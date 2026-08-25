"""Slack integration router — OAuth + Events + Interactions.

Four endpoints:

  GET  /api/v1/integrations/slack/install      kicks off OAuth (admin)
  GET  /api/v1/integrations/slack/callback     OAuth callback
  POST /api/v1/slack/events                    Events API webhook
  POST /api/v1/slack/interactions              Block Kit submissions

The two webhook endpoints (events, interactions) are unauthenticated
in the JWT sense — they're verified via the Slack signing secret.
The OAuth install endpoints require an OrgAdmin JWT (only admins
should be connecting workspaces on a tenant's behalf).

The webhook handlers do the minimum synchronously:
  - verify signature
  - persist the inbound event / answer to the DB
  - return 200 within Slack's 3-second window

Long-running work (sending the next question DM, opening a DM
channel, etc.) is delegated to the worker so we never block Slack's
retry timer.

Note: the actual DM-sending worker lives in `worker/`. This router
just owns the inbound side.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin
from app.config import get_settings
from app.database import get_db_session
from app.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.models.slack_workspace import SlackWorkspace
from app.models.survey import (
    ResponseStatus,
    SlackOptOut,
    SurveyResponse,
)
from app.services.crypto import encrypt_token
from app.services.slack_adapter import (
    InteractionParseError,
    SignatureInvalid,
    is_reminder_action,
    parse_form_payload,
    parse_interaction,
    parse_reminder_interaction,
    verify_signature,
)
from app.services.slack_oauth import (
    OAuthExchangeError,
    OAuthStateError,
    build_authorize_url,
    decode_state,
    encode_state,
    exchange_code,
)
from app.services.survey_flow import (
    SurveyValidationError,
    next_question,
    parse_questions,
    validate_answer,
)

logger = structlog.get_logger()

# Two distinct mounts so the OAuth endpoints sit under /integrations/
# while the webhooks live at /slack/* — matches the URL spec in the
# bot doc §5.3.
integrations_router = APIRouter(prefix="/integrations/slack", tags=["Slack integration"])
slack_router = APIRouter(prefix="/slack", tags=["Slack webhooks"])


# ─── OAuth: install + callback ───────────────────────────────────────


@integrations_router.get("/install")
async def slack_install(user: OrgAdmin) -> RedirectResponse:
    """Kick off the Slack OAuth v2 flow.

    Redirects the admin to Slack's authorize page with a signed
    `state` parameter that carries tenant + user identity. The
    callback verifies it.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot install Slack")

    settings = get_settings()
    if not settings.slack_client_id or not settings.slack_signing_secret:
        raise ConflictError("Slack integration not configured on this deployment")

    state = encode_state(
        signing_secret=settings.slack_signing_secret,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        nonce=uuid.uuid4().hex,
    )
    url = build_authorize_url(
        client_id=settings.slack_client_id,
        redirect_url=settings.slack_oauth_redirect_url,
        state=state,
    )
    return RedirectResponse(url=url, status_code=302)


@integrations_router.get("/callback", response_class=HTMLResponse)
async def slack_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Slack redirects here after the admin approves.

    Verifies state, exchanges code, stores the encrypted token. No
    JWT — identity comes from the signed state.
    """
    settings = get_settings()
    if not settings.slack_signing_secret:
        raise ConflictError("Slack integration not configured")

    try:
        payload = decode_state(signing_secret=settings.slack_signing_secret, state=state)
    except OAuthStateError as exc:
        raise UnauthorizedError(f"state invalid: {exc}")

    tenant_uuid = uuid.UUID(payload["tenant_id"])
    user_uuid = uuid.UUID(payload["user_id"])

    async with httpx.AsyncClient() as client:
        try:
            creds = await exchange_code(
                client=client,
                client_id=settings.slack_client_id,
                client_secret=settings.slack_client_secret,
                redirect_url=settings.slack_oauth_redirect_url,
                code=code,
            )
        except OAuthExchangeError as exc:
            logger.warning("slack_oauth_exchange_failed", error=str(exc))
            raise UnauthorizedError(f"slack code exchange failed: {exc}")

    # Upsert workspace row. If the same team is already installed,
    # update the token and bump installed_at (re-install).
    existing_result = await db.execute(
        select(SlackWorkspace).where(
            SlackWorkspace.tenant_id == tenant_uuid,
            SlackWorkspace.team_id == creds["team_id"],
        )
    )
    workspace = existing_result.scalar_one_or_none()
    encrypted = encrypt_token(creds["bot_access_token"])
    now = datetime.now(UTC)

    if workspace is None:
        workspace = SlackWorkspace(
            tenant_id=tenant_uuid,
            team_id=creds["team_id"],
            team_name=creds["team_name"],
            bot_user_id=creds["bot_user_id"],
            bot_access_token_encrypted=encrypted,
            scopes=",".join(creds["scopes"]),
            installed_by_user_id=user_uuid,
            installed_at=now,
            is_active=True,
        )
        db.add(workspace)
    else:
        workspace.team_name = creds["team_name"]
        workspace.bot_user_id = creds["bot_user_id"]
        workspace.bot_access_token_encrypted = encrypted
        workspace.scopes = ",".join(creds["scopes"])
        workspace.installed_by_user_id = user_uuid
        workspace.installed_at = now
        workspace.uninstalled_at = None
        workspace.is_active = True

    await db.commit()
    logger.info(
        "slack_workspace_installed",
        tenant_id=str(tenant_uuid),
        team_id=creds["team_id"],
    )

    from app.services.oauth_callback_html import oauth_success_response

    return oauth_success_response(
        provider="SLACK",
        display_name=creds.get("team_name") or "Slack",
        extra_data={"team_id": creds["team_id"]},
    )


# ─── Webhooks: events + interactions ─────────────────────────────────


async def _verify_or_401(
    request: Request,
    x_slack_request_timestamp: str | None,
    x_slack_signature: str | None,
    body: bytes,
) -> None:
    settings = get_settings()
    try:
        verify_signature(
            settings.slack_signing_secret,
            x_slack_request_timestamp or "",
            body,
            x_slack_signature or "",
        )
    except SignatureInvalid as exc:
        logger.warning("slack_signature_invalid", error=str(exc))
        raise UnauthorizedError(f"slack signature: {exc}")


@slack_router.post("/events")
async def slack_events(
    request: Request,
    x_slack_request_timestamp: str | None = Header(None),
    x_slack_signature: str | None = Header(None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Slack Events API entry.

    Handles two event types in v0.1:

    1. `url_verification` — Slack's one-time challenge; echo the
       challenge back.
    2. `event_callback` with `type=message`, `channel_type=im` —
       if the user said STOP, write a SlackOptOut row.
    """
    body = await request.body()
    payload = json.loads(body or b"{}")

    # URL verification doesn't include a timestamp/signature pair on
    # the first call from a brand-new app; Slack documents this as an
    # exception. We still call verify when headers present.
    if payload.get("type") != "url_verification":
        await _verify_or_401(request, x_slack_request_timestamp, x_slack_signature, body)

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event = payload.get("event") or {}
    event_type = event.get("type")

    # STOP-to-opt-out path.
    if (
        event_type == "message"
        and event.get("channel_type") == "im"
        and (event.get("text") or "").strip().upper() in {"STOP", "UNSUBSCRIBE"}
        and not event.get("bot_id")
    ):
        team_id = payload.get("team_id") or ""
        slack_user = event.get("user") or ""
        if team_id and slack_user:
            workspace = await _workspace_by_team(db, team_id)
            if workspace is not None:
                await _record_opt_out(db, workspace.tenant_id, slack_user, "STOP reply")
                logger.info(
                    "slack_user_opted_out",
                    tenant_id=str(workspace.tenant_id),
                    slack_user_id=slack_user,
                )

    return {"ok": True}


@slack_router.post("/interactions")
async def slack_interactions(
    request: Request,
    x_slack_request_timestamp: str | None = Header(None),
    x_slack_signature: str | None = Header(None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Slack Block Kit interactive component submissions.

    Body is `application/x-www-form-urlencoded` with `payload=<json>`.
    We unwrap, verify signature, resolve the SurveyResponse, and
    advance the survey-flow state.
    """
    body = await request.body()
    await _verify_or_401(request, x_slack_request_timestamp, x_slack_signature, body)

    try:
        raw_payload = parse_form_payload(body)
    except InteractionParseError as exc:
        logger.warning("slack_payload_unparseable", error=str(exc))
        raise UnauthorizedError(f"payload: {exc}")

    # ── Branch: inline reminder-action buttons (rmd_* action_id) ──
    # Routed before the survey parser because those action_ids are
    # in a different namespace and the survey parser would mis-classify
    # them. The reminder handler is end-to-end: it resolves the actor,
    # writes the DB row, and returns the replace_original Block Kit
    # payload — Slack swaps the DM in place.
    if is_reminder_action(raw_payload):
        try:
            rmd = parse_reminder_interaction(raw_payload)
        except InteractionParseError as exc:
            logger.warning("slack_reminder_parse_failed", error=str(exc))
            raise UnauthorizedError(f"reminder action: {exc}")
        from app.services.reminder_actions import handle_reminder_action

        async with httpx.AsyncClient() as http_client:
            return await handle_reminder_action(
                db,
                client=http_client,
                verb=rmd["verb"],
                target=rmd["target"],
                slack_user_id=rmd["slack_user_id"],
                team_id=rmd["team_id"],
            )

    try:
        parsed = parse_interaction(raw_payload)
    except InteractionParseError as exc:
        logger.warning("slack_interaction_parse_failed", error=str(exc))
        raise UnauthorizedError(f"interaction: {exc}")

    action_type = parsed["action_type"]
    response_id = uuid.UUID(parsed["response_id"])
    question_id = parsed["question_id"]
    value = parsed["value"]

    # answer_select events (radio click / checkbox toggle) don't
    # commit anything — they fire on every interaction, but we only
    # apply on Submit/Skip.
    if action_type == "answer_select":
        return {"ok": True}

    # Resolve the response row + the parent template.
    response_row = await db.execute(select(SurveyResponse).where(SurveyResponse.id == response_id))
    response = response_row.scalar_one_or_none()
    if response is None:
        raise NotFoundError("SurveyResponse", str(response_id))

    if response.status in {
        ResponseStatus.COMPLETED,
        ResponseStatus.OPTED_OUT,
        ResponseStatus.EXPIRED,
    }:
        # Stale submission — quietly OK so Slack doesn't retry forever.
        return {"ok": True, "note": f"response status={response.status.value}"}

    # Load template via the delivery → template join.
    from app.models.survey import SurveyDelivery, SurveyTemplate

    delivery = (
        await db.execute(select(SurveyDelivery).where(SurveyDelivery.id == response.delivery_id))
    ).scalar_one_or_none()
    if delivery is None:
        raise NotFoundError("SurveyDelivery", str(response.delivery_id))

    template = (
        await db.execute(select(SurveyTemplate).where(SurveyTemplate.id == delivery.template_id))
    ).scalar_one_or_none()
    if template is None:
        raise NotFoundError("SurveyTemplate", str(delivery.template_id))

    questions = parse_questions(template.questions_json)
    answers: dict[str, Any] = json.loads(response.answers_json or "{}")

    q = next((q for q in questions if q.id == question_id), None)
    if q is None:
        raise NotFoundError("Question", question_id)

    # Validate ordering — refuse out-of-order submissions.
    step = next_question(questions, answers)
    if step.is_complete or step.question is None or step.question.id != question_id:
        # Slack double-fired or user clicked a stale message; ignore.
        return {"ok": True, "note": "stale submission ignored"}

    # Apply.
    if action_type == "skip":
        if not q.optional:
            return {"ok": True, "note": "skip on non-optional ignored"}
        answers[q.id] = None
    else:  # submit
        try:
            canonical = validate_answer(q, value)
        except SurveyValidationError as exc:
            logger.info(
                "slack_answer_invalid",
                question_id=q.id,
                error=str(exc),
            )
            # User-facing message would normally go back via response_url;
            # for v0.1 we just OK and the worker re-sends.
            return {"ok": True, "note": str(exc)}
        answers[q.id] = canonical

    response.answers_json = json.dumps(answers)
    now = datetime.now(UTC)
    if response.started_at is None:
        response.started_at = now
    if response.status == ResponseStatus.NOT_STARTED:
        response.status = ResponseStatus.IN_PROGRESS

    new_step = next_question(questions, answers)
    if new_step.is_complete:
        response.status = ResponseStatus.COMPLETED
        response.completed_at = now
        delivery.completed_count = (delivery.completed_count or 0) + 1

    await db.commit()

    return {
        "ok": True,
        "is_complete": new_step.is_complete,
        "next_question_id": (new_step.question.id if new_step.question is not None else None),
    }


# ─── Helpers ─────────────────────────────────────────────────────────


async def _workspace_by_team(db: AsyncSession, team_id: str) -> SlackWorkspace | None:
    result = await db.execute(
        select(SlackWorkspace).where(
            SlackWorkspace.team_id == team_id,
            SlackWorkspace.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _record_opt_out(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    slack_user_id: str,
    reason: str,
) -> None:
    """Upsert a SlackOptOut row + suppress in-flight responses."""
    existing = await db.execute(
        select(SlackOptOut).where(
            SlackOptOut.tenant_id == tenant_id,
            SlackOptOut.slack_user_id == slack_user_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(
            SlackOptOut(
                tenant_id=tenant_id,
                slack_user_id=slack_user_id,
                reason=reason,
            )
        )

    # Suppress any in-flight surveys for this user.
    in_flight = (
        (
            await db.execute(
                select(SurveyResponse).where(
                    SurveyResponse.tenant_id == tenant_id,
                    SurveyResponse.slack_user_id == slack_user_id,
                    SurveyResponse.status.in_(
                        [ResponseStatus.NOT_STARTED, ResponseStatus.IN_PROGRESS]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for resp in in_flight:
        resp.status = ResponseStatus.OPTED_OUT

    await db.commit()
