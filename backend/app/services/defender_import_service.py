"""Defender for Cloud Apps import — seeded extraction + inventory promotion (#243).

Ports the AI-SPM "defender-import" prototype: a pasted Microsoft Defender
for Cloud Apps export (no real OCR in v1) is scanned line by line against
`KNOWN_APPS`, a static catalog carrying each app's risk/compliance
enrichment (the same shape as `SaaSVendorProfile`). A matched line becomes
an upserted `DiscoveredDefenderApp` candidate; `accept()` promotes one into
the AI inventory by creating a real `saas_vendor` (`UseCase` +
`SaaSVendorProfile`), reusing `services.saas_vendor_service.create_vendor`
so the vendor lands in the same register the rest of atlas already renders.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.defender_import import DefenderImportStatus, DiscoveredDefenderApp
from app.models.saas_vendor import VendorCategory, VendorDiscoveryMethod
from app.schemas.defender_import import (
    DefenderAppAcceptRequest,
    DefenderAppAcceptResponse,
    DefenderAppStatusUpdate,
    DefenderImportListResponse,
    DefenderImportResponse,
    DiscoveredDefenderAppResponse,
)
from app.schemas.saas_vendor import SaaSVendorCreate
from app.services import saas_vendor_service

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def canonical_name(name: str) -> str:
    """The correlation key: same normalized name = the same app."""
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")


# One entry per app the extractor recognises. `risk_score` and
# `compliance_status` are the "enrichment" the issue's acceptance criteria
# asks for — a real v2 would call an actual risk-intel service; here it's
# the same deterministic-seed convention as `voice_interview_service`'s
# `KNOWN_AI_TOOLS`.
KNOWN_APPS: dict[str, dict[str, object]] = {
    "chatgpt enterprise": {
        "category": VendorCategory.llm_provider,
        "risk_score": 35,
        "certifications": ["SOC 2 Type II"],
        "compliance_status": {
            "euAiAct": "covered",
            "nistAiRmf": "covered",
            "owaspLlm": "partial",
            "iso42001": "gap",
        },
    },
    "grammarly business": {
        "category": VendorCategory.productivity_suite,
        "risk_score": 22,
        "certifications": ["SOC 2 Type II", "ISO 27001"],
        "compliance_status": {
            "euAiAct": "partial",
            "nistAiRmf": "covered",
            "owaspLlm": "gap",
            "iso42001": "gap",
        },
    },
    "notion ai": {
        "category": VendorCategory.productivity_suite,
        "risk_score": 28,
        "certifications": ["SOC 2 Type II"],
        "compliance_status": {
            "euAiAct": "gap",
            "nistAiRmf": "partial",
            "owaspLlm": "gap",
            "iso42001": "gap",
        },
    },
    "github copilot": {
        "category": VendorCategory.developer_tool,
        "risk_score": 18,
        "certifications": ["SOC 2 Type II", "ISO 27001"],
        "compliance_status": {
            "euAiAct": "covered",
            "nistAiRmf": "covered",
            "owaspLlm": "partial",
            "iso42001": "partial",
        },
    },
    "otter.ai": {
        "category": VendorCategory.productivity_suite,
        "risk_score": 41,
        "certifications": ["SOC 2 Type I"],
        "compliance_status": {
            "euAiAct": "gap",
            "nistAiRmf": "gap",
            "owaspLlm": "gap",
            "iso42001": "gap",
        },
    },
    "jasper": {
        "category": VendorCategory.productivity_suite,
        "risk_score": 33,
        "certifications": ["SOC 2 Type II"],
        "compliance_status": {
            "euAiAct": "partial",
            "nistAiRmf": "partial",
            "owaspLlm": "gap",
            "iso42001": "gap",
        },
    },
    "salesforce einstein": {
        "category": VendorCategory.crm_support,
        "risk_score": 26,
        "certifications": ["SOC 2 Type II", "ISO 27001"],
        "compliance_status": {
            "euAiAct": "covered",
            "nistAiRmf": "covered",
            "owaspLlm": "partial",
            "iso42001": "partial",
        },
    },
}

# Matches lines like "ChatGPT Enterprise — 312 active users — 1.2 TB transferred".
_LINE_RE = re.compile(
    r"^\s*(?P<name>[^—\-\n]+?)\s*[—\-]\s*(?P<users>\d+)\s*(?:active\s+)?users?"
    r"\s*[—\-]\s*(?P<volume>[\d.]+)\s*(?P<unit>GB|TB)\s*transferred\s*$",
    re.IGNORECASE,
)

# Bundled fallback export — stands in for a real Defender for Cloud Apps
# screenshot/CSV until v2 wires actual OCR/extraction.
SEED_EXPORT_TEXT = """\
ChatGPT Enterprise — 312 active users — 1.2 TB transferred
Grammarly Business — 178 active users — 340 GB transferred
Notion AI — 96 active users — 78 GB transferred
GitHub Copilot — 54 active users — 12 GB transferred
Otter.ai — 23 active users — 9 GB transferred
"""


def _volume_mb(amount: float, unit: str) -> int:
    return round(amount * 1024) if unit.upper() == "GB" else round(amount * 1024 * 1024)


def _match_known_app(name: str) -> tuple[str, dict[str, object]] | None:
    """Best (longest) `KNOWN_APPS` key contained in `name`, if any."""
    lowered = name.strip().lower()
    matches = [key for key in KNOWN_APPS if key in lowered]
    if not matches:
        return None
    best = max(matches, key=len)
    return best, KNOWN_APPS[best]


def _to_response(app: DiscoveredDefenderApp) -> DiscoveredDefenderAppResponse:
    return DiscoveredDefenderAppResponse(
        id=str(app.id),
        canonical_name=app.canonical_name,
        name=app.name,
        category=app.category,
        users_count=app.users_count,
        data_volume_mb=app.data_volume_mb,
        risk_score=app.risk_score,
        certifications=list(app.certifications or []),
        compliance_status=dict(app.compliance_status or {}),
        status=app.status,
        promoted_vendor_id=str(app.promoted_vendor_id) if app.promoted_vendor_id else None,
        imported_at=app.imported_at,
    )


async def list_apps(db: AsyncSession, tenant_id: uuid.UUID | None) -> DefenderImportListResponse:
    """All imported app candidates for the tenant, highest-risk first."""
    query = select(DiscoveredDefenderApp)
    if tenant_id is not None:
        query = query.where(DiscoveredDefenderApp.tenant_id == tenant_id)
    result = await db.execute(query.order_by(DiscoveredDefenderApp.risk_score.desc()))
    return DefenderImportListResponse(apps=[_to_response(a) for a in result.scalars().all()])


async def import_export(
    db: AsyncSession, tenant_id: uuid.UUID, raw_text: str | None
) -> DefenderImportResponse:
    """Extract known apps from a pasted export and upsert candidate rows.

    Idempotent: a re-import of the same app updates its enrichment/usage
    fields but keeps its existing `status`/`promoted_vendor_id` — a
    human's accept/dismiss survives re-imports, matching
    `mcp_discovery_service.run_scan`.
    """
    text = raw_text if raw_text and raw_text.strip() else SEED_EXPORT_TEXT

    matched: list[tuple[str, int, int, str, dict[str, object]]] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        found = _match_known_app(m.group("name"))
        if found is None:
            continue
        _, enrichment = found
        matched.append(
            (
                m.group("name").strip(),
                int(m.group("users")),
                _volume_mb(float(m.group("volume")), m.group("unit")),
                m.group("name").strip(),
                enrichment,
            )
        )

    result = await db.execute(
        select(DiscoveredDefenderApp).where(DiscoveredDefenderApp.tenant_id == tenant_id)
    )
    existing = {a.canonical_name: a for a in result.scalars().all()}

    created = 0
    updated = 0
    touched: list[DiscoveredDefenderApp] = []
    for display_name, users_count, volume_mb, _, enrichment in matched:
        key = canonical_name(display_name)
        app = existing.get(key)
        if app is None:
            app = DiscoveredDefenderApp(
                tenant_id=tenant_id,
                canonical_name=key,
                status=DefenderImportStatus.pending,
            )
            db.add(app)
            existing[key] = app
            created += 1
        else:
            updated += 1
        category = enrichment["category"]
        app.name = display_name
        app.category = category.value if isinstance(category, VendorCategory) else str(category)
        app.users_count = users_count
        app.data_volume_mb = volume_mb
        app.risk_score = int(enrichment["risk_score"])  # type: ignore[arg-type]
        app.certifications = list(enrichment["certifications"])  # type: ignore[arg-type]
        app.compliance_status = dict(enrichment["compliance_status"])  # type: ignore[arg-type]
        app.imported_at = datetime.now(UTC)
        touched.append(app)

    await db.commit()
    for app in touched:
        await db.refresh(app)
    return DefenderImportResponse(
        matched_lines=len(matched),
        created=created,
        updated=updated,
        apps=[_to_response(a) for a in touched],
    )


async def update_status(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    app_id: uuid.UUID,
    payload: DefenderAppStatusUpdate,
) -> DiscoveredDefenderAppResponse | None:
    """Dismiss (or reset) an imported app candidate. None if not found."""
    query = select(DiscoveredDefenderApp).where(DiscoveredDefenderApp.id == app_id)
    if tenant_id is not None:
        query = query.where(DiscoveredDefenderApp.tenant_id == tenant_id)
    result = await db.execute(query)
    app = result.scalar_one_or_none()
    if app is None:
        return None
    app.status = payload.status
    await db.commit()
    await db.refresh(app)
    return _to_response(app)


async def accept(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    app_id: uuid.UUID,
    payload: DefenderAppAcceptRequest,
) -> DefenderAppAcceptResponse | None:
    """Promote a candidate into the AI inventory as a SaaS vendor.

    None if the app doesn't exist (or belongs to another tenant).
    """
    result = await db.execute(
        select(DiscoveredDefenderApp).where(
            DiscoveredDefenderApp.id == app_id,
            DiscoveredDefenderApp.tenant_id == tenant_id,
        )
    )
    app = result.scalar_one_or_none()
    if app is None:
        return None

    vendor = await saas_vendor_service.create_vendor(
        db,
        tenant_id,
        SaaSVendorCreate(
            name=app.name,
            department=payload.department,
            category=VendorCategory(app.category),
            ai_feature=app.name,
            discovered_via=VendorDiscoveryMethod.integration,
            risk_score=app.risk_score,
            certifications=list(app.certifications or []),
            compliance_status=dict(app.compliance_status or {}),
        ),
    )

    app.status = DefenderImportStatus.accepted
    app.promoted_vendor_id = uuid.UUID(vendor.id)
    await db.commit()
    await db.refresh(app)
    return DefenderAppAcceptResponse(app=_to_response(app), vendor_id=vendor.id)
