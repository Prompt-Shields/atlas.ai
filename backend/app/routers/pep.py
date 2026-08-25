"""PEP router — `/api/v1/pep` (LLM Policy Enforcement Point, issues #237, #238).

  GET   /pep/templates                Any        list the shared template catalog
  GET   /pep/templates/{id}           Any        one template
  GET   /pep/policies                 Any        list the tenant's instances
  GET   /pep/policies/{id}            Any        one instance
  POST  /pep/policies                 OrgAdmin   create an instance directly
  POST  /pep/policies/{template_id}/clone   OrgAdmin   clone a template into a new instance
  POST  /pep/policies/{id}/evaluate   OrgAdmin   run the Test Console simulator
  GET   /pep/policies/{id}/eligibility Any       promotion eligibility snapshot
  POST  /pep/policies/{id}/approve    OrgAdmin   sign off as a required approver role
  POST  /pep/policies/{id}/promote    OrgAdmin   Guideline -> Strict
  POST  /pep/policies/{id}/demote     OrgAdmin   Strict -> Guideline
  POST  /pep/watchdog/tick            (cron)     all-tenant auto-demote sweep

Templates are tenant-agnostic reference data (no tenant filter); instances
are tenant-scoped, matching `routers/saas_vendor.py`'s explicit `tenant_id`
filter (super-admins see all). The lifecycle actions (evaluate/approve/
promote/demote) are gated OrgAdmin+, same as direct create/clone, since each
one mutates instance state (stats, violations, promotion_history) rather
than just reading it.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.config import get_settings
from app.database import get_db_session, get_standalone_session, set_tenant_guc
from app.errors import AppException, ForbiddenError, NotFoundError
from app.models.tenant import Tenant
from app.schemas.policy_enforcement import (
    PolicyApproveRequest,
    PolicyDemoteRequest,
    PolicyEligibilityResponse,
    PolicyEvaluateRequest,
    PolicyEvaluateResponse,
    PolicyInstanceCloneRequest,
    PolicyInstanceCreate,
    PolicyInstanceResponse,
    PolicyPromoteRequest,
    PolicyTemplateResponse,
    WatchdogDemotedItem,
    WatchdogTickResponse,
)
from app.services import policy_enforcement_service as service

logger = structlog.get_logger()

router = APIRouter(prefix="/pep", tags=["Policy Enforcement Point"])


def _tenant_scope(user: AuthUser) -> uuid.UUID | None:
    """None for super-admins (see all tenants), else the caller's tenant id."""
    return None if user.is_super_admin() else user.tenant_id


# ─── Templates (read-only, tenant-agnostic) ───────────────────────────


@router.get("/templates", response_model=list[PolicyTemplateResponse])
async def list_templates(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> list[PolicyTemplateResponse]:
    """The shared, read-only template catalog — same for every tenant."""
    return await service.list_templates(db)


@router.get("/templates/{template_id}", response_model=PolicyTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyTemplateResponse:
    template = await service.get_template_response(db, template_id)
    if template is None:
        raise NotFoundError("Template not found")
    return template


# ─── Instances (tenant-scoped) ─────────────────────────────────────────


@router.get("/policies", response_model=list[PolicyInstanceResponse])
async def list_policies(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> list[PolicyInstanceResponse]:
    """List the tenant's policy instances."""
    return await service.list_instances(db, _tenant_scope(user))


@router.get("/policies/{instance_id}", response_model=PolicyInstanceResponse)
async def get_policy(
    instance_id: uuid.UUID,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyInstanceResponse:
    instance = await service.get_instance(db, _tenant_scope(user), instance_id)
    if instance is None:
        raise NotFoundError("Policy instance not found")
    return instance


@router.post(
    "/policies", response_model=PolicyInstanceResponse, status_code=status.HTTP_201_CREATED
)
async def create_policy(
    payload: PolicyInstanceCreate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyInstanceResponse:
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot create a policy")
    instance = await service.create_instance(
        db, user.tenant_id, payload, created_by_user_id=user.user_id
    )
    if instance is None:
        raise NotFoundError("Template not found")
    return instance


@router.post(
    "/policies/{template_id}/clone",
    response_model=PolicyInstanceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_policy(
    template_id: uuid.UUID,
    payload: PolicyInstanceCloneRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyInstanceResponse:
    """Clone a template into a new tenant instance (Guideline mode by default)."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot clone a policy")
    instance = await service.clone_template(
        db, user.tenant_id, template_id, payload, created_by_user_id=user.user_id
    )
    if instance is None:
        raise NotFoundError("Template not found")
    return instance


# ─── Lifecycle: evaluate / eligibility / approve / promote / demote (#238) ──


@router.post("/policies/{instance_id}/evaluate", response_model=PolicyEvaluateResponse)
async def evaluate_policy_instance(
    instance_id: uuid.UUID,
    payload: PolicyEvaluateRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyEvaluateResponse:
    """Run the instance's detectors against a sample prompt (Test Console).

    Records a `PolicyViolation` and bumps the instance's rolling stats when
    the simulated action would be "block".
    """
    result = await service.evaluate_instance(
        db, _tenant_scope(user), instance_id, payload, evaluated_by_user_id=user.user_id
    )
    if result is None:
        raise NotFoundError("Policy instance not found")
    return result


@router.get("/policies/{instance_id}/eligibility", response_model=PolicyEligibilityResponse)
async def get_policy_eligibility(
    instance_id: uuid.UUID,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyEligibilityResponse:
    """Whether this Guideline instance may promote to Strict right now."""
    result = await service.get_eligibility(db, _tenant_scope(user), instance_id)
    if result is None:
        raise NotFoundError("Policy instance not found")
    return result


@router.post("/policies/{instance_id}/approve", response_model=PolicyEligibilityResponse)
async def approve_policy_promotion(
    instance_id: uuid.UUID,
    payload: PolicyApproveRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyEligibilityResponse:
    """Sign off as one of the template's required-approver roles."""
    try:
        result = await service.approve_promotion(
            db, _tenant_scope(user), instance_id, payload, by=user.email
        )
    except ValueError as exc:
        raise AppException(code="INVALID_APPROVAL", message=str(exc), status_code=400) from exc
    if result is None:
        raise NotFoundError("Policy instance not found")
    return result


@router.post("/policies/{instance_id}/promote", response_model=PolicyInstanceResponse)
async def promote_policy_instance(
    instance_id: uuid.UUID,
    payload: PolicyPromoteRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyInstanceResponse:
    """Guideline → Strict. 400 if eligibility gates (time/FP-rate/approvers) aren't met."""
    try:
        result = await service.promote_instance(
            db, _tenant_scope(user), instance_id, payload, by=user.email
        )
    except ValueError as exc:
        raise AppException(code="NOT_ELIGIBLE", message=str(exc), status_code=400) from exc
    if result is None:
        raise NotFoundError("Policy instance not found")
    return result


@router.post("/policies/{instance_id}/demote", response_model=PolicyInstanceResponse)
async def demote_policy_instance(
    instance_id: uuid.UUID,
    payload: PolicyDemoteRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> PolicyInstanceResponse:
    """Strict → Guideline. Always permitted while currently Strict — the safety valve."""
    try:
        result = await service.demote_instance(
            db, _tenant_scope(user), instance_id, payload, by=user.email
        )
    except ValueError as exc:
        raise AppException(code="NOT_STRICT", message=str(exc), status_code=400) from exc
    if result is None:
        raise NotFoundError("Policy instance not found")
    return result


# ─── Watchdog (cron entry point, not JWT-authenticated) ─────────────────────


@router.post("/watchdog/tick", response_model=WatchdogTickResponse)
async def watchdog_tick(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> WatchdogTickResponse:
    """All-tenant auto-demote sweep, guarded by a shared secret (cron entry point).

    Not JWT-authenticated — mirrors `POST /cost/sync`'s guard convention.
    Returns 503 when no secret is configured, 401 when the ``X-Cron-Secret``
    header is missing or doesn't match. Scans every tenant's Strict (block/
    redact) policy instances and demotes any whose false-positive rate has
    stayed above its configured cap past the grace period. One tenant's
    failure never aborts the sweep.
    """
    configured = get_settings().pep_watchdog_cron_secret
    if not configured:
        raise AppException(
            code="CRON_NOT_CONFIGURED",
            message="pep watchdog not configured",
            status_code=503,
        )
    if x_cron_secret is None or not hmac.compare_digest(x_cron_secret, configured):
        raise AppException(
            code="UNAUTHORIZED",
            message="invalid cron secret",
            status_code=401,
        )

    now = datetime.now(UTC)
    checked = 0
    demoted: list[WatchdogDemotedItem] = []

    async with get_standalone_session() as db:
        tenant_ids = (await db.execute(select(Tenant.id))).scalars().all()

        for tid in tenant_ids:
            await set_tenant_guc(db, tid)
            try:
                tenant_checked, tenant_demoted = await service.run_watchdog_tick_for_tenant(
                    db, tid, now=now
                )
            except Exception as exc:  # noqa: BLE001 — one bad tenant must not abort the sweep.
                logger.warning("pep_watchdog_tenant_failed", tenant_id=str(tid), error=str(exc))
                continue

            checked += tenant_checked
            for item in tenant_demoted:
                logger.info(
                    "pep_watchdog_auto_demoted",
                    tenant_id=str(tid),
                    policy_instance_id=item["policy_instance_id"],
                    reason=item["reason"],
                )
                demoted.append(
                    WatchdogDemotedItem(
                        tenant_id=str(tid),
                        policy_instance_id=item["policy_instance_id"],
                        reason=item["reason"],
                    )
                )

    return WatchdogTickResponse(instances_checked=checked, demoted=demoted)
