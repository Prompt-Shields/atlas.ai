"""Seed demo SaaS Vendor AI data (SP-1, issue #193).

Loads an illustrative-but-realistic set of SaaS AI vendors as `use_cases` +
1:1 `saas_vendor_profiles` rows, marked `is_test_data=True`. Risk scores and
posture fields are demo values (the source, ai-spm-dashboard's `SAAS_VENDORS`,
lives in a separate repo; this port keeps the shape and realism, not the exact
numbers).

Idempotent: a vendor is keyed by its use-case title within the tenant, so
re-running only inserts the ones that are missing.

Usage:
    python -m scripts.seed_saas_vendors --tenant-slug demo
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_standalone_session
from app.models.saas_vendor import (
    SaaSVendorProfile,
    VendorCategory,
    VendorContractStatus,
    VendorDiscoveryMethod,
)
from app.models.tenant import Tenant
from app.models.use_case import UseCase, UseCaseRiskTier, UseCaseSource, UseCaseStatus

# Demo vendors. `name` → use_case.title, `ai_feature` → use_case.tool.
SEED_VENDORS: list[dict] = [
    {
        "name": "ChatGPT Enterprise",
        "ai_feature": "OpenAI GPT-4o",
        "department": "Engineering",
        "category": VendorCategory.llm_provider,
        "discovered_via": VendorDiscoveryMethod.sso,
        "risk_score": 72,
        "models": ["gpt-4o", "gpt-4o-mini"],
        "sub_processors": ["Microsoft Azure"],
        "data_flows": [
            {"description": "User prompts", "classification": "confidential", "trains_on_data": False},
        ],
        "certifications": ["SOC2 Type II", "ISO 27001"],
        "compliance_status": {"euAiAct": "partial", "iso42001": "gap"},
        "dpa": True,
        "training_opt_out": True,
        "contract_status": VendorContractStatus.active,
    },
    {
        "name": "GitHub Copilot",
        "ai_feature": "Copilot (GPT-4o)",
        "department": "Engineering",
        "category": VendorCategory.developer_tool,
        "discovered_via": VendorDiscoveryMethod.integration,
        "risk_score": 48,
        "models": ["gpt-4o"],
        "sub_processors": ["Microsoft Azure", "OpenAI"],
        "data_flows": [
            {"description": "Source code context", "classification": "confidential", "trains_on_data": False},
        ],
        "certifications": ["SOC2 Type II"],
        "compliance_status": {"euAiAct": "partial"},
        "dpa": True,
        "training_opt_out": True,
        "contract_status": VendorContractStatus.active,
    },
    {
        "name": "Google Gemini",
        "ai_feature": "Gemini 1.5 Pro",
        "department": "Marketing",
        "category": VendorCategory.llm_provider,
        "discovered_via": VendorDiscoveryMethod.browser_extension,
        "risk_score": 68,
        "models": ["gemini-1.5-pro"],
        "sub_processors": ["Google Cloud"],
        "data_flows": [
            {"description": "Campaign copy + audience data", "classification": "internal", "trains_on_data": True},
        ],
        "certifications": ["ISO 27001"],
        "compliance_status": {"euAiAct": "gap", "nistAiRmf": "partial"},
        "dpa": False,
        "training_opt_out": False,
        "contract_status": VendorContractStatus.pending_renewal,
    },
    {
        "name": "Notion AI",
        "ai_feature": "Notion AI (Claude + GPT-4)",
        "department": "Operations",
        "category": VendorCategory.productivity_suite,
        "discovered_via": VendorDiscoveryMethod.endpoint,
        "risk_score": 54,
        "models": ["claude-3-5-sonnet", "gpt-4"],
        "sub_processors": ["Anthropic", "OpenAI", "AWS"],
        "data_flows": [
            {"description": "Workspace documents", "classification": "confidential", "trains_on_data": False},
        ],
        "certifications": ["SOC2 Type II"],
        "compliance_status": {"iso42001": "partial"},
        "dpa": True,
        "training_opt_out": False,
        "contract_status": VendorContractStatus.active,
    },
    {
        "name": "Salesforce Einstein",
        "ai_feature": "Einstein GPT",
        "department": "Sales",
        "category": VendorCategory.crm_support,
        "discovered_via": VendorDiscoveryMethod.integration,
        "risk_score": 41,
        "models": ["proprietary"],
        "sub_processors": ["AWS"],
        "data_flows": [
            {"description": "CRM records", "classification": "confidential", "trains_on_data": False},
        ],
        "certifications": ["SOC2 Type II", "ISO 27001", "ISO 42001"],
        "compliance_status": {"euAiAct": "covered", "iso42001": "covered"},
        "dpa": True,
        "training_opt_out": True,
        "contract_status": VendorContractStatus.active,
    },
    {
        "name": "Anthropic Claude (Team)",
        "ai_feature": "Claude 3.5 Sonnet",
        "department": "Legal",
        "category": VendorCategory.llm_provider,
        "discovered_via": VendorDiscoveryMethod.manual,
        "risk_score": 33,
        "models": ["claude-3-5-sonnet"],
        "sub_processors": ["AWS", "Google Cloud"],
        "data_flows": [
            {"description": "Contract review prompts", "classification": "confidential", "trains_on_data": False},
        ],
        "certifications": ["SOC2 Type II", "ISO 27001"],
        "compliance_status": {"euAiAct": "partial", "iso42001": "partial"},
        "dpa": True,
        "training_opt_out": True,
        "contract_status": VendorContractStatus.active,
    },
]


async def seed_vendors(db: AsyncSession, tenant_id) -> int:
    """Idempotently insert the demo vendors for a tenant. Returns count created."""
    existing = set(
        (
            await db.execute(
                select(UseCase.title).where(UseCase.tenant_id == tenant_id)
            )
        ).scalars().all()
    )

    created = 0
    for spec in SEED_VENDORS:
        if spec["name"] in existing:
            continue
        use_case = UseCase(
            tenant_id=tenant_id,
            title=spec["name"],
            tool=spec["ai_feature"],
            department=spec["department"],
            status=UseCaseStatus.ACTIVE,
            risk_tier=_risk_tier(spec["risk_score"]),
            source=UseCaseSource.IMPORT,
            data_classes="[]",
            is_test_data=True,
        )
        db.add(use_case)
        await db.flush()  # assign use_case.id for the profile FK

        db.add(
            SaaSVendorProfile(
                tenant_id=tenant_id,
                use_case_id=use_case.id,
                category=spec["category"],
                discovered_via=spec["discovered_via"],
                risk_score=spec["risk_score"],
                models=spec["models"],
                sub_processors=spec["sub_processors"],
                data_flows=spec["data_flows"],
                certifications=spec["certifications"],
                compliance_status=spec["compliance_status"],
                dpa=spec["dpa"],
                training_opt_out=spec["training_opt_out"],
                contract_status=spec["contract_status"],
            )
        )
        created += 1

    await db.commit()
    return created


def _risk_tier(score: int) -> UseCaseRiskTier:
    if score >= 70:
        return UseCaseRiskTier.HIGH
    if score >= 40:
        return UseCaseRiskTier.MEDIUM
    return UseCaseRiskTier.LOW


async def _main(tenant_slug: str) -> None:
    async with get_standalone_session() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"No tenant with slug {tenant_slug!r} — seed a tenant first.")
        created = await seed_vendors(db, tenant.id)
    print(f"Seeded {created} demo vendor(s) for tenant {tenant_slug!r} "
          f"({len(SEED_VENDORS) - created} already present).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", default="demo", help="Tenant slug to seed into")
    args = parser.parse_args()
    asyncio.run(_main(args.tenant_slug))


if __name__ == "__main__":
    main()
