"""SaaS Vendor AI router — `/api/v1/saas-vendor-ai` (SP-1, issue #191).

The vendor surface over the AI-inventory registry. A vendor is a
`use_cases` row with a 1:1 `saas_vendor_profiles` row; this router is the
list / detail / KPI / CSV / create / update layer. Query + mutation logic
lives in `services.saas_vendor_service`; CSV rendering in
`services.saas_vendor_csv`.

  GET   /saas-vendor-ai              Viewer+   list vendors
  GET   /saas-vendor-ai/kpi          Viewer+   KPI aggregate card
  GET   /saas-vendor-ai/export.csv   Viewer+   CSV download
  GET   /saas-vendor-ai/{id}         Viewer+   one vendor
  POST  /saas-vendor-ai              OrgAdmin  create use-case + profile
  PATCH /saas-vendor-ai/{id}         OrgAdmin  update

Tenant scoping is an explicit `tenant_id` filter (super-admins see all),
matching `routers/use_cases.py`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.schemas.saas_vendor import (
    AssessmentImportCreate,
    AssessmentImportResponse,
    AssessmentRequestCreate,
    AssessmentRequestResponse,
    SaaSVendorCreate,
    SaaSVendorResponse,
    SaaSVendorUpdate,
    VendorKpiResponse,
)
from app.services import saas_vendor_csv, saas_vendor_service

router = APIRouter(prefix="/saas-vendor-ai", tags=["SaaS Vendor AI"])


def _tenant_scope(user: AuthUser) -> uuid.UUID | None:
    """None for super-admins (see all tenants), else the caller's tenant id."""
    return None if user.is_super_admin() else user.tenant_id


# ─── Read ────────────────────────────────────────────────────────────
# Static paths (/kpi, /export.csv) are declared BEFORE /{id} so the path
# param doesn't shadow them.


@router.get("", response_model=list[SaaSVendorResponse])
async def list_vendors(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> list[SaaSVendorResponse]:
    """List the tenant's SaaS AI vendors."""
    return await saas_vendor_service.list_vendors(db, _tenant_scope(user))


@router.get("/kpi", response_model=VendorKpiResponse)
async def vendor_kpi(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> VendorKpiResponse:
    """KPI aggregate: total, high-risk, trains-on-data, no-DPA."""
    return await saas_vendor_service.compute_kpi(db, _tenant_scope(user))


@router.get("/export.csv")
async def export_vendors_csv(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Download all vendors as CSV."""
    vendors = await saas_vendor_service.list_vendors(db, _tenant_scope(user))
    body = saas_vendor_csv.render_vendors_csv(vendors)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="saas-vendor-ai.csv"'},
    )


@router.get("/{vendor_id}", response_model=SaaSVendorResponse)
async def get_vendor(
    vendor_id: uuid.UUID,
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> SaaSVendorResponse:
    """Fetch one vendor by its use-case id."""
    vendor = await saas_vendor_service.get_vendor(db, _tenant_scope(user), vendor_id)
    if vendor is None:
        raise NotFoundError("Vendor not found")
    return vendor


# ─── Write ───────────────────────────────────────────────────────────


@router.post("", response_model=SaaSVendorResponse, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    payload: SaaSVendorCreate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> SaaSVendorResponse:
    """Register a vendor (creates a use-case row + its profile)."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot register a vendor")
    return await saas_vendor_service.create_vendor(db, user.tenant_id, payload)


@router.patch("/{vendor_id}", response_model=SaaSVendorResponse)
async def update_vendor(
    vendor_id: uuid.UUID,
    payload: SaaSVendorUpdate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> SaaSVendorResponse:
    """Update a vendor's use-case and profile fields."""
    vendor = await saas_vendor_service.update_vendor(db, _tenant_scope(user), vendor_id, payload)
    if vendor is None:
        raise NotFoundError("Vendor not found")
    return vendor


# ─── Assessment exchange (SP-1 #192) ─────────────────────────────────


@router.post(
    "/{vendor_id}/request-assessment",
    response_model=AssessmentRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_assessment(
    vendor_id: uuid.UUID,
    payload: AssessmentRequestCreate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> AssessmentRequestResponse:
    """Outbound: request an assessment from the vendor (simulated send)."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot request an assessment")
    result = await saas_vendor_service.create_assessment_request(
        db, user.tenant_id, vendor_id, payload, requested_by_user_id=user.user_id
    )
    if result is None:
        raise NotFoundError("Vendor not found")
    return result


@router.post(
    "/{vendor_id}/import-assessment",
    response_model=AssessmentImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_assessment(
    vendor_id: uuid.UUID,
    payload: AssessmentImportCreate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> AssessmentImportResponse:
    """Inbound: record an imported assessment (peer-shared / trust-center / upload)."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot import an assessment")
    result = await saas_vendor_service.create_assessment_import(
        db, user.tenant_id, vendor_id, payload, imported_by_user_id=user.user_id
    )
    if result is None:
        raise NotFoundError("Vendor not found")
    return result
