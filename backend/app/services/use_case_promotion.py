"""Promotion bridge from the intake registry into the SPM catalogue."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_asset import AIAsset
from app.models.ai_use_case import AIUseCase
from app.models.use_case import UseCase, UseCaseStatus


def _first_data_classification(raw_data_classes: str | None) -> str | None:
    """Return the first valid data-class taxonomy value from JSON text."""
    if not raw_data_classes:
        return None
    try:
        data_classes = json.loads(raw_data_classes)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data_classes, list):
        return None
    for data_class in data_classes:
        if isinstance(data_class, str) and data_class.strip():
            return data_class.strip()
    return None


async def promote_use_case_to_asset(
    db: AsyncSession,
    *,
    use_case: UseCase,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
) -> tuple[AIAsset, AIUseCase, bool]:
    """Create SPM records for an intake use case, or return its prior promotion."""
    promotion_marker = f"promoted_from:{use_case.id}"
    existing_result = await db.execute(
        select(AIUseCase, AIAsset)
        .join(AIAsset, AIUseCase.asset_id == AIAsset.id)
        .where(
            AIUseCase.discovered_via == promotion_marker,
            AIUseCase.tenant_id == tenant_id,
            AIAsset.tenant_id == tenant_id,
        )
    )
    existing = existing_result.first()
    if existing is not None:
        ai_use_case, asset = existing
        return asset, ai_use_case, False

    source_status = (
        use_case.status.value.lower()
        if isinstance(use_case.status, UseCaseStatus)
        else str(use_case.status).lower()
    )
    asset = AIAsset(
        tenant_id=tenant_id,
        org_id=org_id,
        name=use_case.title,
        description=use_case.notes or f"{use_case.tool} — {use_case.department}",
        deployment_status="active" if use_case.status == UseCaseStatus.ACTIVE else "testing",
        model_type=use_case.tool,
        owner_id=use_case.owner_user_id,
        is_third_party=False,
    )
    db.add(asset)
    await db.flush()

    ai_use_case = AIUseCase(
        tenant_id=tenant_id,
        org_id=org_id,
        asset_id=asset.id,
        name=use_case.title,
        department=use_case.department,
        description=use_case.notes,
        business_owner_id=use_case.owner_user_id,
        data_classification=_first_data_classification(use_case.data_classes),
        status=source_status,
        discovered_via=promotion_marker,
    )
    db.add(ai_use_case)

    if use_case.status in {UseCaseStatus.DRAFT, UseCaseStatus.REVIEW}:
        use_case.status = UseCaseStatus.ACTIVE

    await db.flush()
    return asset, ai_use_case, True
