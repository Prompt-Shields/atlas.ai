"""Adapter router — blob ingestion endpoints.

Sensitive endpoints require both JWT and API key (two-factor).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import get_adapter, list_adapters
from app.auth.dependencies import Analyst, SecureUser, get_tenant_db_session
from app.errors import ForbiddenError
from app.models.blob import BlobRecord
from app.schemas.blob import BlobIngestRequest, BlobListResponse, BlobResponse

router = APIRouter(prefix="/adapters", tags=["Adapters"])


@router.get("/available")
async def get_available_adapters(user: Analyst) -> dict:
    """List available input adapters."""
    return {"adapters": list_adapters()}


@router.post("/manual/ingest", response_model=list[BlobResponse], status_code=201)
async def manual_ingest(
    body: BlobIngestRequest,
    user: SecureUser,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> list[BlobResponse]:
    """Ingest blob text via the Manual adapter.

    Requires BOTH JWT token AND X-API-Key header.
    """
    if not user.tenant_id or not user.org_id:
        raise ForbiddenError("Tenant and org context required for ingestion")

    adapter = get_adapter("manual")
    records = await adapter.ingest(
        db,
        tenant_id=user.tenant_id,
        org_id=user.org_id,
        created_by=user.user_id,
        content=body.content,
        title=body.title,
        source_id=body.source_id,
        metadata=body.metadata,
    )

    return [
        BlobResponse(
            id=str(r.id),
            tenant_id=str(r.tenant_id),
            org_id=str(r.org_id),
            source_type=r.source_type,
            source_id=r.source_id,
            title=r.title,
            content_preview=r.content[:200],
            word_count=r.word_count,
            processing_status=r.processing_status,
            batch_id=r.batch_id,
            adapter_name=r.adapter_name,
            created_at=r.created_at,
            is_test_data=r.is_test_data,
        )
        for r in records
    ]


@router.post("/{adapter_name}/ingest", response_model=list[BlobResponse], status_code=201)
async def adapter_ingest(
    adapter_name: str,
    user: SecureUser,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> list[BlobResponse]:
    """Ingest blob text via a named adapter (purview, defender, etc.).

    Requires BOTH JWT token AND X-API-Key header.
    """
    if not user.tenant_id or not user.org_id:
        raise ForbiddenError("Tenant and org context required for ingestion")

    adapter = get_adapter(adapter_name)
    records = await adapter.ingest(
        db,
        tenant_id=user.tenant_id,
        org_id=user.org_id,
        created_by=user.user_id,
    )

    return [
        BlobResponse(
            id=str(r.id),
            tenant_id=str(r.tenant_id),
            org_id=str(r.org_id),
            source_type=r.source_type,
            source_id=r.source_id,
            title=r.title,
            content_preview=r.content[:200],
            word_count=r.word_count,
            processing_status=r.processing_status,
            batch_id=r.batch_id,
            adapter_name=r.adapter_name,
            created_at=r.created_at,
            is_test_data=r.is_test_data,
        )
        for r in records
    ]


@router.get("/blobs", response_model=BlobListResponse)
async def list_blobs(
    user: Analyst,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> BlobListResponse:
    """List ingested blobs (tenant-scoped)."""
    query = select(BlobRecord)
    count_query = select(func.count(BlobRecord.id))

    if not user.is_super_admin():
        query = query.where(BlobRecord.tenant_id == user.tenant_id)
        count_query = count_query.where(BlobRecord.tenant_id == user.tenant_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        query.order_by(BlobRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    blobs = list(result.scalars().all())

    return BlobListResponse(
        blobs=[
            BlobResponse(
                id=str(b.id),
                tenant_id=str(b.tenant_id),
                org_id=str(b.org_id),
                source_type=b.source_type,
                source_id=b.source_id,
                title=b.title,
                content_preview=b.content[:200],
                word_count=b.word_count,
                processing_status=b.processing_status,
                batch_id=b.batch_id,
                adapter_name=b.adapter_name,
                created_at=b.created_at,
                is_test_data=b.is_test_data,
            )
            for b in blobs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
