"""Invite management router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin, SuperAdmin
from app.database import get_db_session
from app.errors import ConflictError, ForbiddenError
from app.schemas.invite import InviteConfirm, InviteCreate, InviteListResponse, InviteResponse
from app.services import invite_service
from app.services.audit import log_audit_event
from app.services.email_service import send_invite_email
from app.services.tenant_service import get_tenant
from app.services.users_csv import (
    ParseError,
    parse_users_csv,
    render_users_import_template,
)

router = APIRouter(prefix="/invites", tags=["Invites"])


@router.post("", response_model=InviteResponse, status_code=201)
async def create_invite(
    body: InviteCreate,
    request: Request,
    user: SuperAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> InviteResponse:
    """Create a tenant invite (super admin only).

    Flow: super admin invites by email -> invite token -> email confirmation
    -> tenant admin sets password -> tenant is activated.
    """
    import uuid

    invite, raw_token = await invite_service.create_invite(
        db,
        email=body.email,
        tenant_id=uuid.UUID(body.tenant_id),
        invited_by=user.user_id,
        role=body.role,
    )

    # Get tenant name for email
    tenant = await get_tenant(db, uuid.UUID(body.tenant_id))

    # Send invitation email
    await send_invite_email(body.email, raw_token, tenant.name)

    # Audit
    await log_audit_event(
        db,
        event_type="invite.created",
        action="create",
        actor_id=user.user_id,
        actor_email=user.email,
        tenant_id=uuid.UUID(body.tenant_id),
        resource_type="invite",
        resource_id=str(invite.id),
        details={"email": body.email, "role": body.role},
        correlation_id=getattr(request.state, "correlation_id", None),
    )

    return InviteResponse(
        id=str(invite.id),
        email=invite.email,
        tenant_id=str(invite.tenant_id),
        role=invite.role,
        is_used=invite.is_used,
        is_revoked=invite.is_revoked,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
    )


@router.post("/confirm")
async def confirm_invite(
    body: InviteConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Confirm an invite with token, set password, and activate tenant.

    This endpoint does NOT require authentication — it uses the invite token.
    Rate limited to prevent brute-force token guessing.
    """
    from app.auth.rate_limit import check_rate_limit

    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"invite_confirm:{client_ip}", max_attempts=5, window_seconds=300)

    result = await invite_service.confirm_invite(
        db,
        token=body.token,
        password=body.password,
        full_name=body.full_name,
    )

    await log_audit_event(
        db,
        event_type="invite.confirmed",
        action="update",
        resource_type="invite",
        details={"user_id": result["user_id"], "tenant_id": result["tenant_id"]},
        correlation_id=getattr(request.state, "correlation_id", None),
    )

    return {"message": "Invite confirmed successfully", **result}


@router.get("", response_model=InviteListResponse)
async def list_invites(
    user: SuperAdmin,
    tenant_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> InviteListResponse:
    """List invites (super admin only)."""
    import uuid

    tid = uuid.UUID(tenant_id) if tenant_id else None
    invites, total = await invite_service.list_invites(
        db, tenant_id=tid, page=page, page_size=page_size
    )

    return InviteListResponse(
        invites=[
            InviteResponse(
                id=str(i.id),
                email=i.email,
                tenant_id=str(i.tenant_id),
                role=i.role,
                is_used=i.is_used,
                is_revoked=i.is_revoked,
                expires_at=i.expires_at,
                created_at=i.created_at,
            )
            for i in invites
        ],
        total=total,
    )


# ─── Bulk CSV import (OrgAdmin tenant-scoped) ────────────────────────


class InviteImportRowError(BaseModel):
    row_number: int
    field: str | None
    message: str


class InviteImportWarning(BaseModel):
    row_number: int
    message: str


class InviteImportResponse(BaseModel):
    """Outcome of POST /invites/import.

    `invited`: rows where an invite was successfully created (+ email queued).
    `errors`: rows that failed CSV validation — NOT processed.
    `warnings`: rows that didn't produce an invite for a non-fatal reason
                (e.g. an active invite already exists for that email +
                tenant — skipped instead of erroring).
    """

    invited: int
    errors: list[InviteImportRowError]
    warnings: list[InviteImportWarning]
    total_rows_seen: int


@router.get("/import-template")
async def get_invite_import_template(user: OrgAdmin) -> Response:
    """Downloadable CSV template — headers + 3 example rows."""
    body = render_users_import_template()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": ('attachment; filename="atlas-user-invites-template.csv"')},
    )


@router.post("/import", response_model=InviteImportResponse)
async def import_invites_csv(
    request: Request,
    user: OrgAdmin,
    file: UploadFile = File(..., description="CSV file matching the template"),
    db: AsyncSession = Depends(get_db_session),
) -> InviteImportResponse:
    """OrgAdmin bulk-creates invites for their own tenant.

    Symmetric to /api/v1/ai-inventory/import (PR #59). Per-row
    failures don't poison the batch; existing-invite collisions are
    warnings, not errors. Emails are sent in-loop (best-effort —
    swallowed on failure so a flaky SMTP doesn't abort the import).
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot import invites.")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    if len(raw_bytes) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file exceeds 2 MB.",
        )
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Uploaded file is not valid UTF-8: {exc}. "
                f"Re-save as UTF-8 (Excel: File → Save As → CSV UTF-8)."
            ),
        )

    result = parse_users_csv(text)
    total_seen = len(result.valid_rows) + _count_unique_error_rows(result.errors)

    # Resolve tenant for the email subject line. Stops the import if
    # the OrgAdmin somehow has a stale tenant_id — defence in depth.
    tenant = await get_tenant(db, user.tenant_id)
    if tenant is None:
        raise ForbiddenError("Tenant not found.")

    warnings: list[InviteImportWarning] = []
    invited = 0

    for parsed in result.valid_rows:
        try:
            invite, raw_token = await invite_service.create_invite(
                db,
                email=parsed.email,
                tenant_id=user.tenant_id,
                invited_by=user.user_id,
                role=parsed.role,
            )
        except ConflictError as exc:
            # Active invite already exists — soft-skip, not an error.
            warnings.append(
                InviteImportWarning(
                    row_number=parsed.row_number,
                    message=(
                        f"{parsed.email}: active invite already exists for "
                        f"this email + tenant — skipped. ({exc})"
                    ),
                )
            )
            continue

        # Best-effort email send. SMTP failures shouldn't abort the
        # import — the invite row exists and the OrgAdmin can re-send
        # via the existing /invites list endpoint in v0.2.
        try:
            await send_invite_email(parsed.email, raw_token, tenant.name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                InviteImportWarning(
                    row_number=parsed.row_number,
                    message=(
                        f"{parsed.email}: invite created but email send "
                        f"failed: {exc}. Re-send from the Invites page."
                    ),
                )
            )

        await log_audit_event(
            db,
            event_type="invite.created",
            action="create",
            actor_id=user.user_id,
            actor_email=user.email,
            tenant_id=user.tenant_id,
            resource_type="invite",
            resource_id=str(invite.id),
            details={
                "email": parsed.email,
                "role": parsed.role,
                "via": "csv_bulk_import",
            },
            correlation_id=getattr(request.state, "correlation_id", None),
        )
        invited += 1

    if invited > 0:
        await db.commit()

    return InviteImportResponse(
        invited=invited,
        errors=[
            InviteImportRowError(row_number=e.row_number, field=e.field, message=e.message)
            for e in result.errors
        ],
        warnings=warnings,
        total_rows_seen=total_seen,
    )


def _count_unique_error_rows(errors: list[ParseError]) -> int:
    return len({e.row_number for e in errors if e.row_number > 0})
