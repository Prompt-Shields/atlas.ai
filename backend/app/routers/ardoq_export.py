"""Ardoq AI-Lens export router — /api/v1/integrations/ardoq (#248).

  GET /integrations/ardoq/manifest   OrgAdmin  per-entity row counts
  GET /integrations/ardoq/export     OrgAdmin  ZIP download of the 9-file bundle

Registered before the generic `integrations` router in main.py so its
literal `/integrations/ardoq/*` paths resolve ahead of that router's
`/integrations/{integration_id}` catch-all (same reasoning as the
Sentinel connector's `/integrations/sentinel/*` routes).
"""

from __future__ import annotations

import io
import uuid
import zipfile

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin
from app.database import get_db_session
from app.schemas.ardoq_export import ArdoqManifestResponse
from app.services.ardoq_export_query import build_ardoq_bundle

router = APIRouter(prefix="/integrations/ardoq", tags=["Integrations"])


def _tenant_scope(user: OrgAdmin) -> uuid.UUID | None:
    """None for super-admins (see all tenants), else the caller's tenant id."""
    return None if user.is_super_admin() else user.tenant_id


@router.get("/manifest", response_model=ArdoqManifestResponse)
async def get_manifest(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> ArdoqManifestResponse:
    """Row counts per entity — drives the frontend manifest table."""
    bundle = await build_ardoq_bundle(db, _tenant_scope(user))
    return ArdoqManifestResponse(generated_at=bundle["generated_at"], totals=bundle["totals"])


@router.get("/export")
async def export_zip(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Download the 9-CSV Ardoq AI-Lens bundle as a ZIP."""
    bundle = await build_ardoq_bundle(db, _tenant_scope(user))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in bundle["files"]:
            zf.writestr(f["name"], f["csv"])
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ardoq-ai-lens-export.zip"'},
    )
