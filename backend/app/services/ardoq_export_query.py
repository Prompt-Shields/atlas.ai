"""ORM adapter for the Ardoq AI-Lens export — pulls rows, shapes dicts.

`app.services.ardoq_export` is a pure-function port that takes plain
dicts (camelCase keys) and renders the 9-file CSV bundle. This module
is the glue: query the AI-SPM component graph (`app.models.spm_entities`)
plus `AIAsset` / `ComplianceAssessment`, shape each row into the dict
shape `export_ardoq_bundle` expects, and hand it off.

`AIAsset` (Application) predates the `spm_entities` graph and has no
`organizational_unit_id` / `data_classification` / `tags` columns, so
those fields are exported empty. Its `owner_id` points at `users.id`,
not `people.id` — resolved here via `Person.user_id`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_asset import AIAsset
from app.models.compliance_assessment import ComplianceAssessment
from app.models.spm_entities import (
    DataStore,
    EntityReference,
    OrganizationalUnit,
    Person,
    TechnicalCapability,
    TechnologyProduct,
    TechnologyService,
)
from app.services.ardoq_export import export_ardoq_bundle


async def _scoped(db: AsyncSession, model: type[Any], tenant_id: uuid.UUID | None) -> list[Any]:
    query = select(model)
    if tenant_id is not None:
        query = query.where(model.tenant_id == tenant_id)
    result = await db.execute(query)
    return list(result.scalars().all())


def _person_dict(p: Person) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "componentName": p.component_name,
        "description": p.description,
        "email": p.email,
        "role": p.role,
        "tags": p.tags or [],
    }


def _org_dict(o: OrganizationalUnit) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "componentName": o.component_name,
        "description": o.description,
        "tags": o.tags or [],
    }


def _data_store_dict(d: DataStore) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "componentName": d.component_name,
        "description": d.description,
        "storeType": d.store_type,
        "dataClassification": d.data_classification,
        "location": d.location,
        "recordCount": d.record_count,
        "refreshFrequency": d.refresh_frequency,
        "retentionPeriod": d.retention_period,
        "encryption": d.encryption,
        "accessControls": d.access_controls,
        "gdprCompliant": d.gdpr_compliant,
        "dataOwnerId": str(d.data_owner_id) if d.data_owner_id else None,
        "lastAudit": d.last_audit.isoformat() if d.last_audit else None,
        "tags": d.tags or [],
    }


def _service_dict(s: TechnologyService) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "componentName": s.component_name,
        "description": s.description,
        "provider": s.provider,
        "serviceType": s.service_type,
        "region": s.region,
        "status": s.status,
        "uptimePct": s.uptime_pct,
        "costPerMonthNOK": s.cost_per_month_nok,
        "securityCertifications": s.security_certifications or [],
        "networkIsolation": s.network_isolation,
        "scalingPolicy": s.scaling_policy,
        "backupStrategy": s.backup_strategy,
        "tags": s.tags or [],
    }


def _product_dict(p: TechnologyProduct) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "componentName": p.component_name,
        "description": p.description,
        "provider": p.provider,
        "version": p.version,
        "modelCategory": p.model_category,
        "parameters": p.parameters,
        "lifecycleStatus": p.lifecycle_status,
        "inputCostPerMTokens": p.input_cost_per_mtokens,
        "outputCostPerMTokens": p.output_cost_per_mtokens,
        "contextWindow": p.context_window,
        "estimatedMonthlySpendNOK": p.estimated_monthly_spend_nok,
        "tags": p.tags or [],
    }


def _capability_dict(c: TechnicalCapability) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "level1": c.level1,
        "level2": c.level2,
        "level3": c.level3,
        "description": c.description,
    }


def _reference_dict(r: EntityReference) -> dict[str, Any]:
    return {
        "sourceId": str(r.source_id),
        "targetId": str(r.target_id),
        "type": r.reference_type,
        "description": r.description,
    }


def _application_dict(a: AIAsset, persons_by_user_id: dict[uuid.UUID, Person]) -> dict[str, Any]:
    owner = persons_by_user_id.get(a.owner_id) if a.owner_id else None
    return {
        "id": str(a.id),
        "componentName": a.name,
        "description": a.description,
        "organizationalUnitId": None,
        "ownerPersonId": str(owner.id) if owner else None,
        "dataClassification": None,
        "deploymentStatus": a.deployment_status,
        "riskScore": a.risk_score,
        "tags": [],
    }


def _assessment_dict(
    ca: ComplianceAssessment, persons_by_user_id: dict[uuid.UUID, Person]
) -> dict[str, Any]:
    approver = persons_by_user_id.get(ca.approved_by_id) if ca.approved_by_id else None
    return {
        "id": str(ca.id),
        "componentName": f"{ca.framework} assessment",
        "description": ca.rationale,
        "assessmentType": ca.framework,
        "subjectApplicationId": str(ca.asset_id),
        "pass": ca.passed,
        "rationale": ca.rationale,
        "reviewDate": ca.review_date.isoformat() if ca.review_date else None,
        "approvalStatus": ca.approval_status,
        "approvedByPersonId": str(approver.id) if approver else None,
        "euAIActRiskLevel": ca.risk_level,
        "tags": [],
    }


async def build_ardoq_bundle(db: AsyncSession, tenant_id: uuid.UUID | None) -> dict[str, Any]:
    """Query the tenant's component graph and assemble the 9-file bundle.

    `tenant_id=None` (super-admin) returns rows across every tenant —
    matches the `_tenant_scope` convention in `routers/saas_vendor.py`.
    """
    persons = await _scoped(db, Person, tenant_id)
    orgs = await _scoped(db, OrganizationalUnit, tenant_id)
    data_stores = await _scoped(db, DataStore, tenant_id)
    services = await _scoped(db, TechnologyService, tenant_id)
    products = await _scoped(db, TechnologyProduct, tenant_id)
    capabilities = await _scoped(db, TechnicalCapability, tenant_id)
    references = await _scoped(db, EntityReference, tenant_id)
    applications = await _scoped(db, AIAsset, tenant_id)
    assessments = await _scoped(db, ComplianceAssessment, tenant_id)

    persons_by_user_id = {p.user_id: p for p in persons if p.user_id is not None}

    entities = {
        "applications": [_application_dict(a, persons_by_user_id) for a in applications],
        "persons": [_person_dict(p) for p in persons],
        "organizational_units": [_org_dict(o) for o in orgs],
        "data_stores": [_data_store_dict(d) for d in data_stores],
        "technology_services": [_service_dict(s) for s in services],
        "technology_products": [_product_dict(p) for p in products],
        "technical_capabilities": [_capability_dict(c) for c in capabilities],
        "compliance_assessments": [_assessment_dict(a, persons_by_user_id) for a in assessments],
        "references": [_reference_dict(r) for r in references],
    }

    return export_ardoq_bundle(entities)
