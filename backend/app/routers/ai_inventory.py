"""AI inventory router — `/api/v1/ai-inventory`.

Three endpoints for Øystein Endal's priority #2 ("Kartlegge
eksisterende bruk" — map existing usage). The use-case CRUD is in
`app/routers/use_cases.py`; this router is the *bulk + aggregate*
layer on top of it.

  GET  /aggregate          Viewer+   Multi-dimensional slice of the
                                     registry. Powers the dashboard
                                     stats card.
  GET  /import-template    Viewer+   Returns a CSV file body the admin
                                     can download as a starting point.
  POST /import             OrgAdmin  Accepts a CSV upload, parses,
                                     validates, and creates UseCase
                                     rows. Per-row failures don't
                                     poison the batch.

Why a separate router instead of more endpoints on /use-cases?
  The /use-cases router is the single-row CRUD surface. Bulk import +
  aggregate are different shapes (file upload, multi-dim grouping)
  that pull in different services. Keeping them together would muddy
  the JSON shape of the existing list/detail endpoints. Cleaner to
  have a sibling router under a different prefix.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError
from app.models.use_case import UseCase
from app.models.user import User
from app.services.ai_inventory_aggregate import (
    compute_inventory_aggregate,
)
from app.services.ai_inventory_csv import (
    ParseError,
    parse_inventory_csv,
    render_import_template,
)

router = APIRouter(prefix="/ai-inventory", tags=["AI inventory"])


# ─── Aggregate response schemas ──────────────────────────────────────


class DimensionCountResponse(BaseModel):
    label: str
    count: int


class InventoryAggregateResponse(BaseModel):
    total: int
    unowned_count: int
    active_high_risk_count: int
    by_status: dict[str, int]
    by_risk_tier: dict[str, int]
    by_source: dict[str, int]
    by_department: list[DimensionCountResponse]
    by_tool: list[DimensionCountResponse]


# ─── Import response schemas ─────────────────────────────────────────


class ImportRowError(BaseModel):
    row_number: int
    field: str | None
    message: str


class InventoryImportWarning(BaseModel):
    row_number: int
    message: str


class ImportResponse(BaseModel):
    """Outcome of POST /import.

    `imported`: rows successfully written.
    `errors`: rows that failed validation (NOT written).
    `warnings`: rows that were written but had non-fatal issues
                (e.g. owner_email didn't match any User — record
                was still created without an owner).
    """

    imported: int
    errors: list[ImportRowError]
    warnings: list[InventoryImportWarning]
    total_rows_seen: int


# ─── Aggregate ───────────────────────────────────────────────────────


@router.get("/aggregate", response_model=InventoryAggregateResponse)
async def get_inventory_aggregate(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> InventoryAggregateResponse:
    """Multi-dim aggregate of the AI use-case registry. Tenant-scoped."""
    tenant_id = None if user.is_super_admin() else user.tenant_id
    agg = await compute_inventory_aggregate(db, tenant_id=tenant_id)
    return InventoryAggregateResponse(
        total=agg.total,
        unowned_count=agg.unowned_count,
        active_high_risk_count=agg.active_high_risk_count,
        by_status=agg.by_status,
        by_risk_tier=agg.by_risk_tier,
        by_source=agg.by_source,
        by_department=[
            DimensionCountResponse(label=d.label, count=d.count) for d in agg.by_department
        ],
        by_tool=[DimensionCountResponse(label=d.label, count=d.count) for d in agg.by_tool],
    )


# ─── Import template ─────────────────────────────────────────────────


@router.get("/import-template")
async def get_import_template(user: AuthUser) -> Response:
    """Return a downloadable CSV template — headers + 3 example rows.

    Auth: any authenticated user. Even Viewers should be able to
    inspect the schema (it's not sensitive — it's the same info as
    the API docs).
    """
    body = render_import_template()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": ('attachment; filename="atlas-ai-inventory-template.csv"')},
    )


# ─── Import ──────────────────────────────────────────────────────────


@router.post("/import", response_model=ImportResponse)
async def import_inventory_csv(
    user: OrgAdmin,
    file: UploadFile = File(..., description="CSV file matching the import template"),
    db: AsyncSession = Depends(get_db_session),
) -> ImportResponse:
    """Bulk-create use cases from a CSV upload.

    The router does three things on top of the pure parser:
      1. Reads + decodes the upload (UTF-8 with BOM tolerance).
      2. Resolves owner_email → owner_user_id by looking up Users in
         the caller's tenant. Unknown emails produce a warning, not
         an error — the row is still created without an owner.
      3. Inserts UseCase rows. We use one transaction for the whole
         batch so partial-failure is impossible: either every valid
         row is committed, or none are (e.g. on a DB-level failure).
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot import use cases.")

    # ── Read upload ──────────────────────────────────────────────
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    # Reasonable file size cap — MAX_ROWS_PER_IMPORT * ~500 bytes/row
    # ≈ 500KB; allow 2MB for slack. Prevents memory blowups from a
    # malformed upload.
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
                f"Re-save your CSV as UTF-8 (Excel: File → Save As → CSV UTF-8)."
            ),
        )

    # ── Parse + validate ─────────────────────────────────────────
    result = parse_inventory_csv(text)
    total_seen = len(result.valid_rows) + _count_unique_error_rows(result.errors)

    # ── Resolve owner emails ─────────────────────────────────────
    needed_emails = {row.owner_email for row in result.valid_rows if row.owner_email}
    email_to_user_id: dict[str, uuid.UUID] = {}
    if needed_emails:
        users_rows = (
            await db.execute(
                select(User.id, User.email)
                .where(User.tenant_id == user.tenant_id)
                .where(User.email.in_(list(needed_emails)))
            )
        ).all()
        email_to_user_id = {email: uid for uid, email in users_rows}

    warnings: list[InventoryImportWarning] = []
    imported = 0

    # ── Insert rows ──────────────────────────────────────────────
    for parsed in result.valid_rows:
        owner_user_id = None
        if parsed.owner_email:
            owner_user_id = email_to_user_id.get(parsed.owner_email)
            if owner_user_id is None:
                warnings.append(
                    InventoryImportWarning(
                        row_number=parsed.row_number,
                        message=(
                            f"owner_email {parsed.owner_email!r} did not match "
                            f"any active atlas user in your tenant — created "
                            f"without an owner."
                        ),
                    )
                )

        row = UseCase(
            tenant_id=user.tenant_id,
            title=parsed.title,
            tool=parsed.tool,
            department=parsed.department,
            owner_user_id=owner_user_id,
            status=parsed.status,
            risk_tier=parsed.risk_tier,
            source=parsed.source,
            data_classes=json.dumps(parsed.data_classes),
            frequency=parsed.frequency,
            notes=parsed.notes,
        )
        db.add(row)
        imported += 1

    if imported > 0:
        await db.commit()

    return ImportResponse(
        imported=imported,
        errors=[
            ImportRowError(row_number=e.row_number, field=e.field, message=e.message)
            for e in result.errors
        ],
        warnings=warnings,
        total_rows_seen=total_seen,
    )


# ─── Helpers ─────────────────────────────────────────────────────────


def _count_unique_error_rows(errors: list[ParseError]) -> int:
    """A single row can produce multiple ParseErrors (one per failing
    field). For `total_rows_seen` we want to count it once.
    """
    return len({e.row_number for e in errors if e.row_number > 0})
