"""Survey engine router — /api/v1/surveys/*.

Endpoints (PR B scope — Slack-specific transport is PR C):

  GET    /surveys/templates                    list active templates
  POST   /surveys/templates/seed-defaults      idempotent foundation seed
  POST   /surveys/dispatches                   create + populate recipients
  GET    /surveys/dispatches                   list with completion counts
  GET    /surveys/dispatches/{id}              detail
  GET    /surveys/responses?delivery_id=       list per dispatch
  GET    /surveys/responses/{id}               detail
  GET    /surveys/responses/{id}/next          peek next question
  POST   /surveys/responses/{id}/answer        submit an answer
  DELETE /surveys/responses/{id}               GDPR right-to-erasure

Tenant-scoped throughout. Cross-tenant access returns 404 (don't leak
existence). Soft delete patterns from PR A reused where applicable.

The dispatch worker (Slack delivery) runs in `worker/`. It picks up
SurveyDelivery rows where status=PENDING and flips them through
SENDING → SENT. PR C wires that worker.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.survey import (
    DeliveryStatus,
    ResponseStatus,
    SurveyDelivery,
    SurveyResponse,
    SurveyTemplate,
)
from app.schemas.survey import (
    AnswerSubmit,
    AudienceFilter,
    DispatchCreate,
    DispatchListResponse,
    DispatchResponse,
    NextQuestionResponse,
    SurveyResponseListResponse,
    SurveyResponseRecord,
    SurveyTemplateListResponse,
    SurveyTemplateResponse,
)
from app.services.survey_audience import (
    AudienceValidationError,
    label_for_filter,
    resolve_recipients,
)
from app.services.survey_flow import (
    FOUNDATION_TEMPLATE_DESCRIPTION,
    FOUNDATION_TEMPLATE_NAME,
    FOUNDATION_TEMPLATE_SLUG,
    FOUNDATION_TEMPLATE_VERSION,
    SurveyValidationError,
    foundation_template_json,
    next_question,
    parse_questions,
    validate_answer,
)

router = APIRouter(prefix="/surveys", tags=["Surveys"])


# ─── Helpers ─────────────────────────────────────────────────────────


def _safe_json_loads(raw: str | None, fallback: Any = None) -> Any:
    if fallback is None:
        fallback = {}
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _serialise_template(t: SurveyTemplate) -> SurveyTemplateResponse:
    return SurveyTemplateResponse(
        id=str(t.id),
        tenant_id=str(t.tenant_id),
        slug=t.slug,
        name=t.name,
        version=t.version,
        description=t.description,
        questions=_safe_json_loads(t.questions_json, []),
        is_active=t.is_active,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _serialise_dispatch(d: SurveyDelivery, template: SurveyTemplate | None) -> DispatchResponse:
    audience_raw = _safe_json_loads(d.audience_filter_json, {"mode": "all"})
    return DispatchResponse(
        id=str(d.id),
        tenant_id=str(d.tenant_id),
        template_id=str(d.template_id),
        template_slug=template.slug if template else "",
        template_version=template.version if template else "",
        name=d.name,
        audience_label=d.audience_label,
        audience=AudienceFilter(
            mode=audience_raw.get("mode", "all"),
            values=audience_raw.get("values", []),
        ),
        channel=d.channel,
        status=d.status,
        sent_by_user_id=str(d.sent_by_user_id) if d.sent_by_user_id else None,
        recipient_count=d.recipient_count,
        completed_count=d.completed_count,
        new_registration_count=d.new_registration_count,
        created_at=d.created_at,
        dispatched_at=d.dispatched_at,
        completed_at=d.completed_at,
    )


def _serialise_response(r: SurveyResponse) -> SurveyResponseRecord:
    return SurveyResponseRecord(
        id=str(r.id),
        tenant_id=str(r.tenant_id),
        delivery_id=str(r.delivery_id),
        user_id=str(r.user_id) if r.user_id else None,
        slack_user_id=r.slack_user_id,
        recipient_email=r.recipient_email,
        status=r.status,
        answers=_safe_json_loads(r.answers_json, {}),
        started_at=r.started_at,
        completed_at=r.completed_at,
        registered_as_use_case_id=(
            str(r.registered_as_use_case_id) if r.registered_as_use_case_id else None
        ),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


async def _tenant_template(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    slug: str,
    version: str,
) -> SurveyTemplate | None:
    result = await db.execute(
        select(SurveyTemplate).where(
            SurveyTemplate.tenant_id == tenant_id,
            SurveyTemplate.slug == slug,
            SurveyTemplate.version == version,
        )
    )
    return result.scalar_one_or_none()


def _count_applicable(questions: list, answers: dict[str, Any]) -> int:
    """Total questions that *would* be asked given the current answer set.

    Used for the progress indicator in the bot DM.
    """
    from app.services.survey_flow import eval_condition

    return sum(1 for q in questions if eval_condition(q.condition, answers))


# ─── Templates ───────────────────────────────────────────────────────


@router.get("/templates", response_model=SurveyTemplateListResponse)
async def list_templates(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> SurveyTemplateListResponse:
    """List active templates available to the caller's tenant."""
    query = select(SurveyTemplate).where(SurveyTemplate.is_active.is_(True))
    if not user.is_super_admin():
        query = query.where(SurveyTemplate.tenant_id == user.tenant_id)
    query = query.order_by(SurveyTemplate.created_at.desc())

    rows = list((await db.execute(query)).scalars().all())
    return SurveyTemplateListResponse(
        templates=[_serialise_template(r) for r in rows],
        total=len(rows),
    )


@router.post(
    "/templates/seed-defaults",
    status_code=status.HTTP_201_CREATED,
    response_model=SurveyTemplateResponse,
)
async def seed_default_templates(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> SurveyTemplateResponse:
    """Idempotent: create the foundation v1 template for the caller's tenant.

    Safe to call multiple times. Returns the existing row if it's
    already seeded. Frontend's *Send survey* modal calls this on first
    open to guarantee the template exists.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot seed templates")

    existing = await _tenant_template(
        db,
        user.tenant_id,
        FOUNDATION_TEMPLATE_SLUG,
        FOUNDATION_TEMPLATE_VERSION,
    )
    if existing is not None:
        return _serialise_template(existing)

    record = SurveyTemplate(
        tenant_id=user.tenant_id,
        slug=FOUNDATION_TEMPLATE_SLUG,
        version=FOUNDATION_TEMPLATE_VERSION,
        name=FOUNDATION_TEMPLATE_NAME,
        description=FOUNDATION_TEMPLATE_DESCRIPTION,
        questions_json=foundation_template_json(),
        is_active=True,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _serialise_template(record)


# ─── Dispatches ──────────────────────────────────────────────────────


@router.post(
    "/dispatches",
    status_code=status.HTTP_201_CREATED,
    response_model=DispatchResponse,
)
async def create_dispatch(
    payload: DispatchCreate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DispatchResponse:
    """Create a survey dispatch.

    Resolves the audience filter into recipient identities and
    creates one `SurveyResponse` row per recipient (NOT_STARTED).
    The delivery row stays in PENDING until the Slack worker picks
    it up (PR C).
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot dispatch surveys")

    # 1. Resolve template (must be seeded for this tenant).
    template = await _tenant_template(
        db,
        user.tenant_id,
        payload.template_slug,
        payload.template_version,
    )
    if template is None:
        raise NotFoundError(
            "SurveyTemplate",
            f"{payload.template_slug}@{payload.template_version}",
        )
    if not template.is_active:
        raise ConflictError(
            f"Template {payload.template_slug}@{payload.template_version} is not active"
        )

    # 2. Resolve recipients.
    try:
        recipients = await resolve_recipients(
            db,
            user.tenant_id,
            payload.audience.model_dump(),
        )
    except AudienceValidationError:
        raise  # Already an AppException with 400 status.

    # 3. Create delivery row.
    audience_dict = payload.audience.model_dump()
    audience_json = json.dumps(audience_dict)
    label = label_for_filter(audience_dict)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    default_name = f"{template.name} — {today}"

    delivery = SurveyDelivery(
        tenant_id=user.tenant_id,
        template_id=template.id,
        name=payload.name or default_name,
        audience_filter_json=audience_json,
        audience_label=label,
        channel=payload.channel,
        status=DeliveryStatus.PENDING,
        sent_by_user_id=user.user_id,
        recipient_count=len(recipients),
        completed_count=0,
        new_registration_count=0,
    )
    db.add(delivery)
    await db.flush()  # populate delivery.id for FK use below

    # 4. Create one SurveyResponse row per recipient.
    for r in recipients:
        db.add(
            SurveyResponse(
                tenant_id=user.tenant_id,
                delivery_id=delivery.id,
                user_id=r.user_id,
                recipient_email=r.email,
                status=ResponseStatus.NOT_STARTED,
                answers_json="{}",
            )
        )

    await db.commit()
    await db.refresh(delivery)
    return _serialise_dispatch(delivery, template)


@router.get("/dispatches", response_model=DispatchListResponse)
async def list_dispatches(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> DispatchListResponse:
    """List dispatches for the tenant, newest first."""
    base = select(SurveyDelivery)
    base_count = select(func.count(SurveyDelivery.id))
    if not user.is_super_admin():
        base = base.where(SurveyDelivery.tenant_id == user.tenant_id)
        base_count = base_count.where(SurveyDelivery.tenant_id == user.tenant_id)

    total = int((await db.execute(base_count)).scalar() or 0)
    rows = list(
        (
            await db.execute(
                base.order_by(SurveyDelivery.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    # Bulk-fetch the related templates for slug/version display.
    template_ids = {r.template_id for r in rows}
    templates_by_id: dict[uuid.UUID, SurveyTemplate] = {}
    if template_ids:
        result = await db.execute(select(SurveyTemplate).where(SurveyTemplate.id.in_(template_ids)))
        for t in result.scalars().all():
            templates_by_id[t.id] = t

    return DispatchListResponse(
        dispatches=[_serialise_dispatch(d, templates_by_id.get(d.template_id)) for d in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/dispatches/{dispatch_id}", response_model=DispatchResponse)
async def get_dispatch(
    user: AuthUser,
    dispatch_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> DispatchResponse:
    result = await db.execute(select(SurveyDelivery).where(SurveyDelivery.id == dispatch_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("SurveyDelivery", str(dispatch_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("SurveyDelivery", str(dispatch_id))

    template_result = await db.execute(
        select(SurveyTemplate).where(SurveyTemplate.id == record.template_id)
    )
    template = template_result.scalar_one_or_none()
    return _serialise_dispatch(record, template)


# ─── Responses ───────────────────────────────────────────────────────


@router.get("/responses", response_model=SurveyResponseListResponse)
async def list_responses(
    user: AuthUser,
    delivery_id: uuid.UUID | None = Query(None),
    response_status: ResponseStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> SurveyResponseListResponse:
    base = select(SurveyResponse)
    base_count = select(func.count(SurveyResponse.id))
    if not user.is_super_admin():
        base = base.where(SurveyResponse.tenant_id == user.tenant_id)
        base_count = base_count.where(SurveyResponse.tenant_id == user.tenant_id)
    if delivery_id is not None:
        base = base.where(SurveyResponse.delivery_id == delivery_id)
        base_count = base_count.where(SurveyResponse.delivery_id == delivery_id)
    if response_status is not None:
        base = base.where(SurveyResponse.status == response_status)
        base_count = base_count.where(SurveyResponse.status == response_status)

    total = int((await db.execute(base_count)).scalar() or 0)
    rows = list(
        (
            await db.execute(
                base.order_by(SurveyResponse.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return SurveyResponseListResponse(
        responses=[_serialise_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/responses/{response_id}", response_model=SurveyResponseRecord)
async def get_response(
    user: AuthUser,
    response_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> SurveyResponseRecord:
    result = await db.execute(select(SurveyResponse).where(SurveyResponse.id == response_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("SurveyResponse", str(response_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("SurveyResponse", str(response_id))
    return _serialise_response(record)


@router.get("/responses/{response_id}/next", response_model=NextQuestionResponse)
async def peek_next_question(
    user: AuthUser,
    response_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> NextQuestionResponse:
    """Inspect what question would be asked next, without mutating state.

    The bot DM handler calls this to render the initial Block Kit
    when a new response is opened.
    """
    record = await _get_response_authorised(db, user, response_id)
    template = await _get_response_template(db, record)

    questions = parse_questions(template.questions_json)
    answers: dict[str, Any] = _safe_json_loads(record.answers_json, {})

    step = next_question(questions, answers)
    return NextQuestionResponse(
        response_id=str(record.id),
        is_complete=step.is_complete,
        next_question=_question_to_dict(step.question) if step.question else None,
        answered_count=len(answers),
        total_applicable=_count_applicable(questions, answers),
    )


@router.post("/responses/{response_id}/answer", response_model=NextQuestionResponse)
async def submit_answer(
    payload: AnswerSubmit,
    user: AuthUser,
    response_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> NextQuestionResponse:
    """Submit an answer to the current question. Advances the flow.

    Authenticated path. The bot DM uses this endpoint via service-
    account auth (PR C); the dashboard uses the same endpoint via
    the calling user's JWT for the hosted email-fallback web survey
    in v0.2.
    """
    record = await _get_response_authorised(db, user, response_id)
    if record.status in {
        ResponseStatus.COMPLETED,
        ResponseStatus.OPTED_OUT,
        ResponseStatus.EXPIRED,
    }:
        raise ConflictError(f"Cannot submit answer — response status is {record.status.value}")

    template = await _get_response_template(db, record)
    questions = parse_questions(template.questions_json)
    answers: dict[str, Any] = _safe_json_loads(record.answers_json, {})

    # Find the question being answered.
    q = next((q for q in questions if q.id == payload.question_id), None)
    if q is None:
        raise NotFoundError("Question", payload.question_id)

    # Validate that it's the question we'd ask next — prevents
    # out-of-order submissions and replay games.
    step = next_question(questions, answers)
    if step.is_complete:
        raise ConflictError("Survey already complete; no more questions to answer")
    assert step.question is not None  # for type-checker; guaranteed above
    if step.question.id != payload.question_id:
        raise ConflictError(
            f"Out-of-order submission: expected question '{step.question.id}', "
            f"got '{payload.question_id}'"
        )

    # Validate and canonicalise the answer.
    try:
        canonical = validate_answer(q, payload.value)
    except SurveyValidationError as exc:
        # 400 — same status as Pydantic validation errors elsewhere.
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Store.
    if canonical is not None or q.type == "freeText":
        answers[q.id] = canonical
    elif not q.optional:
        # Shouldn't reach here — validate_answer would have thrown.
        raise ConflictError(f"Refusing to store empty answer for required question '{q.id}'")
    else:
        # Optional freeText with empty → record None to satisfy the
        # "id present in answers" check used by next_question.
        answers[q.id] = None

    record.answers_json = json.dumps(answers)

    # Status / timestamps.
    now = datetime.now(UTC)
    if record.started_at is None:
        record.started_at = now
    if record.status == ResponseStatus.NOT_STARTED:
        record.status = ResponseStatus.IN_PROGRESS

    # Compute next step after the new answer.
    new_step = next_question(questions, answers)
    if new_step.is_complete:
        record.status = ResponseStatus.COMPLETED
        record.completed_at = now
        # Bump the delivery's completed_count.
        delivery_result = await db.execute(
            select(SurveyDelivery).where(SurveyDelivery.id == record.delivery_id)
        )
        delivery = delivery_result.scalar_one_or_none()
        if delivery is not None:
            delivery.completed_count = (delivery.completed_count or 0) + 1

    await db.commit()
    await db.refresh(record)

    return NextQuestionResponse(
        response_id=str(record.id),
        is_complete=new_step.is_complete,
        next_question=(_question_to_dict(new_step.question) if new_step.question else None),
        answered_count=len(answers),
        total_applicable=_count_applicable(questions, answers),
    )


@router.delete(
    "/responses/{response_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_response(
    user: OrgAdmin,
    response_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """GDPR right-to-erasure.

    Hard-deletes the response row + its answers. The PR C Slack
    integration also deletes the associated DM via `chat.delete` —
    that side-effect lives in the worker, not here.

    The associated UseCase (if any) keeps existing, but its
    `dispatched_from_response_id` becomes a dangling pointer until
    the migration adds the SET NULL constraint (PR B migration does
    add it).
    """
    record = await _get_response_authorised(db, user, response_id)

    # If response is linked to a delivery whose completed_count was
    # already bumped, decrement so the recent-surveys card stays in
    # sync.
    if record.status == ResponseStatus.COMPLETED:
        delivery_result = await db.execute(
            select(SurveyDelivery).where(SurveyDelivery.id == record.delivery_id)
        )
        delivery = delivery_result.scalar_one_or_none()
        if delivery is not None and delivery.completed_count > 0:
            delivery.completed_count -= 1

    await db.delete(record)
    await db.commit()


# ─── Internal helpers ────────────────────────────────────────────────


async def _get_response_authorised(
    db: AsyncSession,
    user: Any,
    response_id: uuid.UUID,
) -> SurveyResponse:
    result = await db.execute(select(SurveyResponse).where(SurveyResponse.id == response_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError("SurveyResponse", str(response_id))
    if not user.is_super_admin() and record.tenant_id != user.tenant_id:
        raise NotFoundError("SurveyResponse", str(response_id))
    return record


async def _get_response_template(db: AsyncSession, response: SurveyResponse) -> SurveyTemplate:
    delivery_result = await db.execute(
        select(SurveyDelivery).where(SurveyDelivery.id == response.delivery_id)
    )
    delivery = delivery_result.scalar_one_or_none()
    if delivery is None:
        raise NotFoundError("SurveyDelivery", str(response.delivery_id))

    template_result = await db.execute(
        select(SurveyTemplate).where(SurveyTemplate.id == delivery.template_id)
    )
    template = template_result.scalar_one_or_none()
    if template is None:
        raise NotFoundError("SurveyTemplate", str(delivery.template_id))
    return template


def _question_to_dict(question: Any) -> dict[str, Any]:
    """Serialise a survey_flow.Question dataclass to a JSON-safe dict."""
    return {
        "id": question.id,
        "prompt": question.prompt,
        "type": question.type,
        "options": list(question.options),
        "optional": question.optional,
        "condition": question.condition,
    }
