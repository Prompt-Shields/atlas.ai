"""Ardoq AI Lens 9-CSV export bundle.

Pure-function port of the dashboard's `lib/entities/ardoq-export.ts`.
Same column shapes, same RFC 4180 quoting, same CRLF row endings —
output is byte-equivalent to the dashboard's `/api/ardoq/export`
endpoint for identical inputs.

Design choices (matches policy_evaluator.py / policy_promotion.py):

- **Dict-based interface, no Pydantic dependency.** Module ships before
  M1 lands the AI-SPM ORM models. Caller passes plain dicts (each entity
  list is a list of dicts), gets CSV strings back. When M1 lands, wrap
  with a typed shim that calls ``.model_dump()`` on every ORM record
  before passing in.
- **camelCase input keys** match the wire format. The 9 exporters look
  for ``componentName``, ``ownerPersonId``, ``organizationalUnitId``,
  etc., and degrade gracefully when an optional key is absent.
- **Cross-entity FK resolution at export time** — Application's "Owner"
  column resolves ``ownerPersonId`` against the persons list, so renaming
  a person automatically re-flows through every export that references
  them. No state synchronisation needed.

Typical use::

    from app.services.ardoq_export import export_ardoq_bundle

    bundle = export_ardoq_bundle({
        "applications": app_dicts,
        "persons": person_dicts,
        "organizational_units": org_dicts,
        "data_stores": store_dicts,
        "technology_services": svc_dicts,
        "technology_products": product_dicts,
        "technical_capabilities": cap_dicts,
        "compliance_assessments": ca_dicts,
        "references": ref_dicts,
    })
    bundle["files"]  # list of {"name": str, "csv": str}
    bundle["totals"]  # row counts per entity
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from csv import writer as csv_writer
from datetime import UTC, datetime
from typing import Any

# ─── CSV primitives ──────────────────────────────────────────────────


def _csv(header: list[str], rows: Iterable[list[Any]]) -> str:
    """Render a CSV string with CRLF line endings (Excel-friendly).

    RFC 4180 quoting is handled by the stdlib csv module — wraps cells
    containing commas / quotes / CR / LF in quotes and doubles up
    internal quotes.
    """
    buf = io.StringIO()
    w = csv_writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return buf.getvalue()


def _cell(value: Any) -> str:
    """Coerce a value into a CSV cell string. None → empty."""
    if value is None:
        return ""
    return str(value)


def _join_tags(tags: list[str] | None) -> str:
    return ",".join(tags) if tags else ""


def _index_by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a dict of id → record for FK resolution."""
    return {item["id"]: item for item in items if item.get("id")}


# ─── 01 Technical Capabilities ───────────────────────────────────────


def export_technical_capabilities_csv(capabilities: list[dict[str, Any]]) -> str:
    rows = [
        [
            c.get("level1", ""),
            c.get("level2") or "",
            c.get("level3") or "",
            c.get("description", ""),
            c.get("id", ""),
        ]
        for c in capabilities
    ]
    return _csv(
        ["Level 1", "Level 2", "Level 3", "Description", "Custom ID"],
        rows,
    )


# ─── 02 Applications ─────────────────────────────────────────────────
# Department resolved from organizationalUnitId → org.componentName
# Owner resolved from ownerPersonId → person.componentName


def export_applications_csv(
    applications: list[dict[str, Any]],
    organizational_units: list[dict[str, Any]],
    persons: list[dict[str, Any]],
) -> str:
    orgs_idx = _index_by_id(organizational_units)
    persons_idx = _index_by_id(persons)

    rows: list[list[Any]] = []
    for a in applications:
        org_id = a.get("organizationalUnitId") or a.get("organizational_unit_id")
        owner_id = a.get("ownerPersonId") or a.get("owner_person_id")

        rows.append(
            [
                a.get("componentName", ""),
                a.get("description", ""),
                a.get("id", ""),
                orgs_idx.get(org_id or "", {}).get("componentName", ""),
                persons_idx.get(owner_id or "", {}).get("componentName", ""),
                a.get("dataClassification") or a.get("data_classification") or "",
                a.get("deploymentStatus") or a.get("deployment_status", ""),
                _cell(a.get("riskScore", a.get("risk_score", ""))),
                _join_tags(a.get("tags")),
            ],
        )
    return _csv(
        [
            "Component Name",
            "Description",
            "Custom ID",
            "Department",
            "Owner",
            "Data Classification",
            "Deployment Status",
            "Risk Score",
            "Tags",
        ],
        rows,
    )


# ─── 03 Technology Products ──────────────────────────────────────────


def export_technology_products_csv(products: list[dict[str, Any]]) -> str:
    rows = [
        [
            p.get("componentName", ""),
            p.get("description", ""),
            p.get("id", ""),
            p.get("provider", ""),
            p.get("version", ""),
            p.get("modelCategory") or p.get("model_category", ""),
            p.get("parameters") or "",
            p.get("lifecycleStatus") or p.get("lifecycle_status", ""),
            p.get("inputCostPerMTokens") or p.get("input_cost_per_m_tokens") or "",
            p.get("outputCostPerMTokens") or p.get("output_cost_per_m_tokens") or "",
            p.get("contextWindow") or p.get("context_window") or "",
            _cell(p.get("estimatedMonthlySpendNOK") or p.get("estimated_monthly_spend_nok") or ""),
            _join_tags(p.get("tags")),
        ]
        for p in products
    ]
    return _csv(
        [
            "Component Name",
            "Description",
            "Custom ID",
            "Provider",
            "Version",
            "Model Category",
            "Parameters",
            "Lifecycle Status",
            "Input Cost Per M Tokens",
            "Output Cost Per M Tokens",
            "Context Window",
            "Estimated Monthly Spend NOK",
            "Tags",
        ],
        rows,
    )


# ─── 04 Technology Services ──────────────────────────────────────────


def export_technology_services_csv(services: list[dict[str, Any]]) -> str:
    rows: list[list[Any]] = []
    for s in services:
        uptime = s.get("uptimePct", s.get("uptime_pct"))
        certs = s.get("securityCertifications") or s.get("security_certifications") or []
        rows.append(
            [
                s.get("componentName", ""),
                s.get("description", ""),
                s.get("id", ""),
                s.get("provider", ""),
                s.get("serviceType") or s.get("service_type", ""),
                s.get("region", ""),
                s.get("status", ""),
                f"{uptime:.2f}" if isinstance(uptime, (int, float)) else "",
                _cell(s.get("costPerMonthNOK", s.get("cost_per_month_nok", ""))),
                "; ".join(certs),
                s.get("networkIsolation") or s.get("network_isolation") or "",
                s.get("scalingPolicy") or s.get("scaling_policy") or "",
                s.get("backupStrategy") or s.get("backup_strategy") or "",
                _join_tags(s.get("tags")),
            ],
        )
    return _csv(
        [
            "Component Name",
            "Description",
            "Custom ID",
            "Provider",
            "Service Type",
            "Region",
            "Status",
            "Uptime Pct",
            "Cost Per Month NOK",
            "Security Certifications",
            "Network Isolation",
            "Scaling Policy",
            "Backup Strategy",
            "Tags",
        ],
        rows,
    )


# ─── 05 People ───────────────────────────────────────────────────────


def export_people_csv(persons: list[dict[str, Any]]) -> str:
    rows = [
        [
            p.get("componentName", ""),
            p.get("description", ""),
            p.get("id", ""),
            p.get("email", ""),
            p.get("role", ""),
            _join_tags(p.get("tags")),
        ]
        for p in persons
    ]
    return _csv(
        ["Component Name", "Description", "Custom ID", "Email", "Role", "Tags"],
        rows,
    )


# ─── 06 Organizational Units ─────────────────────────────────────────


def export_organizations_csv(organizational_units: list[dict[str, Any]]) -> str:
    rows = [
        [
            o.get("componentName", ""),
            o.get("description", ""),
            o.get("id", ""),
            _join_tags(o.get("tags")),
        ]
        for o in organizational_units
    ]
    return _csv(
        ["Component Name", "Description", "Custom ID", "Tags"],
        rows,
    )


# ─── 07 Data Stores ──────────────────────────────────────────────────
# Data Owner resolved from dataOwnerId → person.componentName


def export_data_stores_csv(
    data_stores: list[dict[str, Any]],
    persons: list[dict[str, Any]],
) -> str:
    persons_idx = _index_by_id(persons)
    rows: list[list[Any]] = []
    for d in data_stores:
        owner_id = d.get("dataOwnerId") or d.get("data_owner_id")
        rows.append(
            [
                d.get("componentName", ""),
                d.get("description", ""),
                d.get("id", ""),
                d.get("storeType") or d.get("store_type", ""),
                d.get("dataClassification") or d.get("data_classification", ""),
                d.get("location", ""),
                d.get("recordCount") or d.get("record_count") or "",
                d.get("refreshFrequency") or d.get("refresh_frequency") or "",
                d.get("retentionPeriod") or d.get("retention_period") or "",
                d.get("encryption") or "",
                d.get("accessControls") or d.get("access_controls") or "",
                "true" if d.get("gdprCompliant", d.get("gdpr_compliant", False)) else "false",
                persons_idx.get(owner_id or "", {}).get("componentName", ""),
                d.get("lastAudit") or d.get("last_audit") or "",
                _join_tags(d.get("tags")),
            ],
        )
    return _csv(
        [
            "Component Name",
            "Description",
            "Custom ID",
            "Store Type",
            "Data Classification",
            "Location",
            "Record Count",
            "Refresh Frequency",
            "Retention Period",
            "Encryption",
            "Access Controls",
            "GDPR Compliant",
            "Data Owner",
            "Last Audit",
            "Tags",
        ],
        rows,
    )


# ─── 08 Compliance Assessments ───────────────────────────────────────
# Subject resolved from subjectApplicationId → app.componentName
# Approved By resolved from approvedByPersonId → person.componentName


def export_compliance_assessments_csv(
    assessments: list[dict[str, Any]],
    applications: list[dict[str, Any]],
    persons: list[dict[str, Any]],
) -> str:
    apps_idx = _index_by_id(applications)
    persons_idx = _index_by_id(persons)

    rows: list[list[Any]] = []
    for a in assessments:
        subject_id = a.get("subjectApplicationId") or a.get("subject_application_id") or ""
        approver_id = a.get("approvedByPersonId") or a.get("approved_by_person_id")
        rows.append(
            [
                a.get("componentName", ""),
                a.get("description", ""),
                a.get("id", ""),
                a.get("assessmentType") or a.get("assessment_type", ""),
                apps_idx.get(subject_id, {}).get("componentName", subject_id),
                "true" if a.get("pass", a.get("passed", False)) else "false",
                a.get("rationale", ""),
                a.get("reviewDate") or a.get("review_date", ""),
                a.get("approvalStatus") or a.get("approval_status", ""),
                persons_idx.get(approver_id or "", {}).get("componentName", ""),
                a.get("euAIActRiskLevel") or a.get("eu_ai_act_risk_level") or "",
                _join_tags(a.get("tags")),
            ],
        )
    return _csv(
        [
            "Component Name",
            "Description",
            "Custom ID",
            "Assessment Type",
            "Subject Application",
            "Pass",
            "Rationale",
            "Review Date",
            "Approval Status",
            "Approved By",
            "EU AI Act Risk Level",
            "Tags",
        ],
        rows,
    )


# ─── 09 References ───────────────────────────────────────────────────


def export_references_csv(references: list[dict[str, Any]]) -> str:
    rows = [
        [
            r.get("sourceId") or r.get("source_id", ""),
            r.get("targetId") or r.get("target_id", ""),
            r.get("type", ""),
            r.get("description", ""),
        ]
        for r in references
    ]
    return _csv(
        ["Source Custom ID", "Target Custom ID", "Reference Type", "Description"],
        rows,
    )


# ─── Bundle ──────────────────────────────────────────────────────────


def export_ardoq_bundle(entities: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Assemble the full 9-file bundle with totals + timestamp.

    Args:
        entities: dict with keys ``applications``, ``persons``,
            ``organizational_units``, ``data_stores``, ``technology_services``,
            ``technology_products``, ``technical_capabilities``,
            ``compliance_assessments``, ``references``. Each value is a
            list of entity dicts (camelCase or snake_case keys both
            accepted). Missing keys default to empty lists.

    Returns:
        ``{
            "files": [{"name": str, "csv": str}, ...],  # 9 entries
            "generated_at": str,                        # ISO timestamp
            "totals": {entity_name: count, ...},
        }``
    """
    apps = entities.get("applications") or []
    persons = entities.get("persons") or []
    orgs = entities.get("organizational_units") or []
    data_stores = entities.get("data_stores") or []
    services = entities.get("technology_services") or []
    products = entities.get("technology_products") or []
    capabilities = entities.get("technical_capabilities") or []
    assessments = entities.get("compliance_assessments") or []
    references = entities.get("references") or []

    files = [
        {
            "name": "01_technical_capabilities.csv",
            "csv": export_technical_capabilities_csv(capabilities),
        },
        {
            "name": "02_applications.csv",
            "csv": export_applications_csv(apps, orgs, persons),
        },
        {
            "name": "03_technology_products.csv",
            "csv": export_technology_products_csv(products),
        },
        {
            "name": "04_technology_services.csv",
            "csv": export_technology_services_csv(services),
        },
        {
            "name": "05_people.csv",
            "csv": export_people_csv(persons),
        },
        {
            "name": "06_organization.csv",
            "csv": export_organizations_csv(orgs),
        },
        {
            "name": "07_data_stores.csv",
            "csv": export_data_stores_csv(data_stores, persons),
        },
        {
            "name": "08_compliance_assessments.csv",
            "csv": export_compliance_assessments_csv(assessments, apps, persons),
        },
        {
            "name": "09_references.csv",
            "csv": export_references_csv(references),
        },
    ]

    totals = {
        "technical_capabilities": len(capabilities),
        "applications": len(apps),
        "technology_products": len(products),
        "technology_services": len(services),
        "people": len(persons),
        "organizations": len(orgs),
        "data_stores": len(data_stores),
        "compliance_assessments": len(assessments),
        "references": len(references),
    }

    return {
        "files": files,
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": totals,
    }
