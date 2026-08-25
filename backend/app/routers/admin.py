"""Admin router — super admin testing tools and system management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SuperAdmin
from app.database import get_db_session
from app.services.testing_service import purge_test_data, run_e2e_test_pipeline

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/test/run-pipeline")
async def run_test_pipeline(
    request: Request,
    user: SuperAdmin,
    tenant_id: str | None = None,
    org_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Run E2E test pipeline (super admin only).

    Executes: blob insertion -> risk analysis -> correlation -> dispatch.
    All data is marked as TEST and tenant-scoped.
    """
    # Use provided IDs or require them
    tid = uuid.UUID(tenant_id) if tenant_id else user.tenant_id
    oid = uuid.UUID(org_id) if org_id else user.org_id

    if not tid or not oid:
        from app.errors import AppException

        raise AppException(
            code="MISSING_PARAMS",
            message="tenant_id and org_id are required. Provide as query params.",
            status_code=400,
        )

    result = await run_e2e_test_pipeline(
        db,
        tenant_id=tid,
        org_id=oid,
        user_id=user.user_id,
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    return result


@router.post("/test/purge")
async def purge_test(
    request: Request,
    user: SuperAdmin,
    tenant_id: str | None = None,
    confirm: bool = False,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Purge test data (super admin only).

    Requires confirm=true query parameter for safety.
    Provide tenant_id to scope purge, or omit for global purge.
    """
    if not confirm:
        from app.errors import AppException

        raise AppException(
            code="CONFIRMATION_REQUIRED",
            message="Purge requires confirmation. Add ?confirm=true to proceed. This will permanently delete all test data.",
            status_code=400,
        )

    tid = uuid.UUID(tenant_id) if tenant_id else None
    counts = await purge_test_data(
        db,
        user_id=user.user_id,
        tenant_id=tid,
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    return {"message": "Test data purged", "counts": counts}
