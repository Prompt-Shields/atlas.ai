"""SaaS Vendor AI — service layer (SP-1, issue #191).

A "vendor" is a `use_cases` row that carries a 1:1 `saas_vendor_profiles`
row (the profile's PRESENCE is the discriminator). This module is the
query/mutation layer under `routers/saas_vendor.py`: list, get, KPI
aggregate, create, and update — all tenant-scoped via an explicit
`tenant_id` filter (mirrors `routers/use_cases.py`).

`ai_feature` maps to `use_case.tool`; `name` to `use_case.title`;
`department`/`owner_user_id`/`source` are reused from the use-case row.
The vendor-only fields live on the profile.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.saas_vendor import (
    AssessmentStatus,
    SaaSVendorProfile,
    VendorAssessmentImport,
    VendorAssessmentRequest,
)
from app.models.use_case import UseCase, UseCaseRiskTier, UseCaseSource, UseCaseStatus
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

# risk_score is 0–100; a vendor at or above this is "high risk" for the KPI.
HIGH_RISK_THRESHOLD = 70


def _risk_tier_for(score: int) -> UseCaseRiskTier:
    """Derive the shared use-case risk tier from the 0-100 vendor score."""
    if score >= HIGH_RISK_THRESHOLD:
        return UseCaseRiskTier.HIGH
    if score >= 40:
        return UseCaseRiskTier.MEDIUM
    return UseCaseRiskTier.LOW


def _trains_on_data(profile: SaaSVendorProfile) -> bool:
    """True if any of the vendor's data flows trains on customer data."""
    return any(bool(f.get("trains_on_data")) for f in (profile.data_flows or []))


def to_response(
    use_case: UseCase, profile: SaaSVendorProfile, *, has_assessment: bool
) -> SaaSVendorResponse:
    """Merge a use-case row + its profile into the API response shape."""
    return SaaSVendorResponse(
        id=str(use_case.id),
        name=use_case.title,
        department=use_case.department,
        owner_user_id=str(use_case.owner_user_id) if use_case.owner_user_id else None,
        source=use_case.source.value,
        has_assessment=has_assessment,
        category=profile.category,
        ai_feature=use_case.tool,
        discovered_via=profile.discovered_via,
        risk_score=profile.risk_score,
        models=list(profile.models or []),
        sub_processors=list(profile.sub_processors or []),
        data_flows=list(profile.data_flows or []),
        certifications=list(profile.certifications or []),
        compliance_status=dict(profile.compliance_status or {}),
        dpa=profile.dpa,
        training_opt_out=profile.training_opt_out,
        contract_status=profile.contract_status,
        last_reviewed_at=profile.last_reviewed_at,
    )


async def _assessed_use_case_ids(db: AsyncSession, use_case_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Subset of the given use-case ids that have a request OR import."""
    if not use_case_ids:
        return set()
    assessed: set[uuid.UUID] = set()
    for model in (VendorAssessmentRequest, VendorAssessmentImport):
        result = await db.execute(
            select(model.use_case_id).where(model.use_case_id.in_(use_case_ids))
        )
        assessed.update(result.scalars().all())
    return assessed


def _vendor_query(tenant_id: uuid.UUID | None):
    """Base SELECT joining use-cases to their vendor profile (inner)."""
    query = select(UseCase, SaaSVendorProfile).join(
        SaaSVendorProfile, SaaSVendorProfile.use_case_id == UseCase.id
    )
    if tenant_id is not None:
        query = query.where(UseCase.tenant_id == tenant_id)
    return query


async def list_vendors(db: AsyncSession, tenant_id: uuid.UUID | None) -> list[SaaSVendorResponse]:
    """All vendors for the tenant (a use-case with a profile), newest first."""
    result = await db.execute(_vendor_query(tenant_id).order_by(UseCase.created_at.desc()))
    rows = list(result.all())
    assessed = await _assessed_use_case_ids(db, [uc.id for uc, _ in rows])
    return [to_response(uc, profile, has_assessment=uc.id in assessed) for uc, profile in rows]


async def get_vendor(
    db: AsyncSession, tenant_id: uuid.UUID | None, vendor_id: uuid.UUID
) -> SaaSVendorResponse | None:
    """One vendor by its use-case id, or None if it isn't a vendor for this tenant."""
    result = await db.execute(_vendor_query(tenant_id).where(UseCase.id == vendor_id))
    row = result.first()
    if row is None:
        return None
    use_case, profile = row
    assessed = await _assessed_use_case_ids(db, [use_case.id])
    return to_response(use_case, profile, has_assessment=use_case.id in assessed)


async def compute_kpi(db: AsyncSession, tenant_id: uuid.UUID | None) -> VendorKpiResponse:
    """Aggregate KPI card: total, high-risk, trains-on-data, no-DPA."""
    result = await db.execute(_vendor_query(tenant_id))
    profiles = [profile for _, profile in result.all()]
    return VendorKpiResponse(
        total=len(profiles),
        high_risk=sum(1 for p in profiles if p.risk_score >= HIGH_RISK_THRESHOLD),
        trains_on_data=sum(1 for p in profiles if _trains_on_data(p)),
        no_dpa=sum(1 for p in profiles if not p.dpa),
    )


async def create_vendor(
    db: AsyncSession, tenant_id: uuid.UUID, payload: SaaSVendorCreate
) -> SaaSVendorResponse:
    """Create a use-case row + its 1:1 vendor profile in one transaction."""
    use_case = UseCase(
        tenant_id=tenant_id,
        title=payload.name,
        tool=payload.ai_feature,
        department=payload.department,
        owner_user_id=uuid.UUID(payload.owner_user_id) if payload.owner_user_id else None,
        status=UseCaseStatus.ACTIVE,
        risk_tier=_risk_tier_for(payload.risk_score),
        source=UseCaseSource.FORM,
        data_classes="[]",
    )
    db.add(use_case)
    await db.flush()  # assign use_case.id before the profile FK references it

    profile = SaaSVendorProfile(
        tenant_id=tenant_id,
        use_case_id=use_case.id,
        category=payload.category,
        discovered_via=payload.discovered_via,
        risk_score=payload.risk_score,
        models=list(payload.models),
        sub_processors=list(payload.sub_processors),
        data_flows=[f.model_dump() for f in payload.data_flows],
        certifications=list(payload.certifications),
        compliance_status=dict(payload.compliance_status),
        dpa=payload.dpa,
        training_opt_out=payload.training_opt_out,
        contract_status=payload.contract_status,
        last_reviewed_at=payload.last_reviewed_at,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(use_case)
    await db.refresh(profile)
    return to_response(use_case, profile, has_assessment=False)


async def update_vendor(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    vendor_id: uuid.UUID,
    payload: SaaSVendorUpdate,
) -> SaaSVendorResponse | None:
    """Patch use-case + profile fields. None if not a vendor for this tenant."""
    result = await db.execute(_vendor_query(tenant_id).where(UseCase.id == vendor_id))
    row = result.first()
    if row is None:
        return None
    use_case, profile = row

    fields = payload.model_dump(exclude_unset=True)

    # Use-case–backed fields.
    if "name" in fields:
        use_case.title = fields["name"]
    if "department" in fields:
        use_case.department = fields["department"]
    if "ai_feature" in fields:
        use_case.tool = fields["ai_feature"]
    if "owner_user_id" in fields:
        owner = fields["owner_user_id"]
        use_case.owner_user_id = uuid.UUID(owner) if owner else None
    if "risk_score" in fields:
        use_case.risk_tier = _risk_tier_for(fields["risk_score"])

    # Profile fields (scalars pass straight through; the shaped ones convert).
    profile_scalars = (
        "category",
        "discovered_via",
        "risk_score",
        "models",
        "sub_processors",
        "certifications",
        "compliance_status",
        "dpa",
        "training_opt_out",
        "contract_status",
        "last_reviewed_at",
    )
    for key in profile_scalars:
        if key in fields:
            setattr(profile, key, fields[key])
    if "data_flows" in fields:
        profile.data_flows = [f.model_dump() for f in payload.data_flows or []]

    await db.commit()
    await db.refresh(use_case)
    await db.refresh(profile)
    assessed = await _assessed_use_case_ids(db, [use_case.id])
    return to_response(use_case, profile, has_assessment=use_case.id in assessed)


async def _vendor_exists(
    db: AsyncSession, tenant_id: uuid.UUID | None, vendor_id: uuid.UUID
) -> bool:
    """True if vendor_id is a vendor (use-case with a profile) for this tenant."""
    result = await db.execute(_vendor_query(tenant_id).where(UseCase.id == vendor_id))
    return result.first() is not None


async def create_assessment_request(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    vendor_id: uuid.UUID,
    payload: AssessmentRequestCreate,
    requested_by_user_id: uuid.UUID | None,
) -> AssessmentRequestResponse | None:
    """Outbound 'request assessment' — simulated send (status ``sent`` + token).

    Returns None if the vendor isn't visible to this tenant.
    """
    if not await _vendor_exists(db, tenant_id, vendor_id):
        return None

    record = VendorAssessmentRequest(
        tenant_id=tenant_id,
        use_case_id=vendor_id,
        questionnaire=payload.questionnaire,
        status=AssessmentStatus.sent,
        token=secrets.token_urlsafe(24),
        requested_by_user_id=requested_by_user_id,
        sent_at=datetime.now(UTC),
        notes=payload.notes,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return AssessmentRequestResponse(
        id=str(record.id),
        use_case_id=str(record.use_case_id),
        questionnaire=record.questionnaire,
        status=record.status,
        token=record.token,
        sent_at=record.sent_at,
        received_at=record.received_at,
        notes=record.notes,
    )


async def create_assessment_import(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    vendor_id: uuid.UUID,
    payload: AssessmentImportCreate,
    imported_by_user_id: uuid.UUID | None,
) -> AssessmentImportResponse | None:
    """Inbound 'import assessment' — peer-shared / trust-center / upload.

    Returns None if the vendor isn't visible to this tenant.
    """
    if not await _vendor_exists(db, tenant_id, vendor_id):
        return None

    record = VendorAssessmentImport(
        tenant_id=tenant_id,
        use_case_id=vendor_id,
        source=payload.source,
        reference=payload.reference,
        payload=payload.payload,
        imported_by_user_id=imported_by_user_id,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return AssessmentImportResponse(
        id=str(record.id),
        use_case_id=str(record.use_case_id),
        source=record.source,
        reference=record.reference,
        payload=record.payload,
        imported_at=record.imported_at,
    )
