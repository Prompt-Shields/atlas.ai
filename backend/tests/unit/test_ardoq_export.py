"""Unit tests for app.services.ardoq_export.

Pins the 9-CSV bundle's column order, header strings, and FK
resolution behaviour against the expected Ardoq AI Lens import schema.
The dashboard's ``lib/entities/ardoq-export.ts`` produces equivalent
output for identical inputs.

Run with: ``pytest tests/unit/test_ardoq_export.py --noconftest``
"""

from __future__ import annotations

import pytest

from app.services.ardoq_export import (
    export_applications_csv,
    export_ardoq_bundle,
    export_compliance_assessments_csv,
    export_data_stores_csv,
    export_organizations_csv,
    export_people_csv,
    export_references_csv,
    export_technical_capabilities_csv,
    export_technology_products_csv,
    export_technology_services_csv,
)

# ─── CSV format invariants ───────────────────────────────────────────


class TestCsvFormat:
    def test_uses_crlf_line_endings(self):
        csv = export_organizations_csv([])
        # Empty store: just the header + trailing CRLF
        assert csv == "Component Name,Description,Custom ID,Tags\r\n"

    def test_quotes_cells_with_commas(self):
        csv = export_organizations_csv(
            [
                {
                    "id": "org-1",
                    "componentName": "Legal, Compliance & Risk",
                    "description": "dept",
                    "tags": [],
                },
            ],
        )
        # Comma in cell forces quoting
        assert '"Legal, Compliance & Risk"' in csv

    def test_quotes_cells_with_quotes_and_doubles_them(self):
        csv = export_organizations_csv(
            [
                {
                    "id": "org-1",
                    "componentName": 'Say "hi"',
                    "description": "dept",
                    "tags": [],
                },
            ],
        )
        assert '"Say ""hi"""' in csv

    def test_none_renders_as_empty_string(self):
        # description=None should not crash; should produce an empty cell
        csv = export_organizations_csv(
            [{"id": "org-1", "componentName": "X", "description": None, "tags": None}],
        )
        # Headers ending in `,Tags\r\n`, then row should have empty desc cell
        assert "X,,org-1,\r\n" in csv


# ─── 01 Technical capabilities ───────────────────────────────────────


class TestTechnicalCapabilities:
    def test_header_columns(self):
        csv = export_technical_capabilities_csv([])
        assert csv.startswith("Level 1,Level 2,Level 3,Description,Custom ID\r\n")

    def test_hierarchy_levels_render_correctly(self):
        csv = export_technical_capabilities_csv(
            [
                {"id": "cap-ai", "level1": "Artificial Intelligence", "description": "Root"},
                {
                    "id": "cap-llm",
                    "level1": "Artificial Intelligence",
                    "level2": "LLM",
                    "description": "Large language models",
                },
                {
                    "id": "cap-text",
                    "level1": "Artificial Intelligence",
                    "level2": "LLM",
                    "level3": "Text Generation",
                    "description": "Text gen",
                },
            ],
        )
        # Root row has empty Level 2 + Level 3
        assert "Artificial Intelligence,,,Root,cap-ai\r\n" in csv
        # Level 2 row has empty Level 3
        assert "Artificial Intelligence,LLM,,Large language models,cap-llm\r\n" in csv
        # Level 3 row has all three populated
        assert "Artificial Intelligence,LLM,Text Generation,Text gen,cap-text\r\n" in csv


# ─── 02 Applications: FK resolution ──────────────────────────────────


class TestApplications:
    def test_owner_and_department_resolved_via_fk(self):
        apps = [
            {
                "id": "app-1",
                "componentName": "Legal Bot",
                "description": "review contracts",
                "organizationalUnitId": "org-legal",
                "ownerPersonId": "person-sarah",
                "dataClassification": "confidential",
                "deploymentStatus": "Active",
                "riskScore": 70,
                "tags": ["ai-system", "legal"],
            },
        ]
        orgs = [{"id": "org-legal", "componentName": "Legal"}]
        persons = [{"id": "person-sarah", "componentName": "Sarah Chen"}]

        csv = export_applications_csv(apps, orgs, persons)
        # Owner column shows resolved name, not the FK
        assert "Sarah Chen" in csv
        assert "Legal Bot,review contracts,app-1,Legal,Sarah Chen,confidential,Active,70" in csv
        # FK should not appear as the value (it's been resolved)
        assert "person-sarah," not in csv

    def test_missing_fk_renders_as_empty_cell(self):
        apps = [
            {
                "id": "app-1",
                "componentName": "X",
                "description": "y",
                "organizationalUnitId": "org-missing",
                "deploymentStatus": "Active",
                "riskScore": 0,
                "tags": [],
            },
        ]
        csv = export_applications_csv(apps, [], [])
        # Department column empty (FK didn't resolve)
        assert "X,y,app-1,,,,Active,0,\r\n" in csv

    def test_camelcase_and_snake_case_keys_both_work(self):
        apps_camel = [
            {
                "id": "app-1",
                "componentName": "X",
                "description": "y",
                "ownerPersonId": "p1",
                "deploymentStatus": "Active",
                "riskScore": 50,
                "tags": [],
            },
        ]
        apps_snake = [
            {
                "id": "app-1",
                "componentName": "X",
                "description": "y",
                "owner_person_id": "p1",
                "deployment_status": "Active",
                "risk_score": 50,
                "tags": [],
            },
        ]
        persons = [{"id": "p1", "componentName": "Alice"}]

        camel_csv = export_applications_csv(apps_camel, [], persons)
        snake_csv = export_applications_csv(apps_snake, [], persons)
        assert camel_csv == snake_csv

    def test_tags_joined_with_commas(self):
        apps = [
            {
                "id": "app-1",
                "componentName": "X",
                "description": "y",
                "deploymentStatus": "Active",
                "riskScore": 0,
                "tags": ["a", "b", "c"],
            },
        ]
        csv = export_applications_csv(apps, [], [])
        # Multiple tags become a quoted comma-separated cell
        assert '"a,b,c"' in csv


# ─── 03 Technology Products ──────────────────────────────────────────


class TestTechnologyProducts:
    def test_renders_all_columns(self):
        products = [
            {
                "id": "tp-gpt4o",
                "componentName": "GPT-4o",
                "description": "OpenAI flagship",
                "provider": "OpenAI",
                "version": "1.0",
                "modelCategory": "LLM",
                "parameters": "1.8 trillion",
                "lifecycleStatus": "Production",
                "inputCostPerMTokens": "$2.50",
                "outputCostPerMTokens": "$10.00",
                "contextWindow": "128K tokens",
                "estimatedMonthlySpendNOK": 45000,
                "tags": ["ai-model", "openai"],
            },
        ]
        csv = export_technology_products_csv(products)
        assert "GPT-4o" in csv
        assert "OpenAI" in csv
        assert "1.8 trillion" in csv
        assert "45000" in csv


# ─── 04 Technology Services ──────────────────────────────────────────


class TestTechnologyServices:
    def test_uptime_pct_formatted_to_2_decimals(self):
        services = [
            {
                "id": "svc-1",
                "componentName": "X",
                "description": "y",
                "provider": "Azure",
                "serviceType": "Cloud Infrastructure",
                "region": "westeurope",
                "status": "Active",
                "uptimePct": 99.997,
                "securityCertifications": [],
                "tags": [],
            },
        ]
        csv = export_technology_services_csv(services)
        # Should round to 2 decimals: 100.00 (since .997 → rounded up by f-string)
        assert "100.00" in csv

    def test_security_certs_joined_with_semicolon(self):
        services = [
            {
                "id": "svc-1",
                "componentName": "X",
                "description": "y",
                "provider": "Azure",
                "serviceType": "Cloud Infrastructure",
                "region": "x",
                "status": "Active",
                "securityCertifications": ["ISO 27001", "SOC 2", "GDPR"],
                "tags": [],
            },
        ]
        csv = export_technology_services_csv(services)
        # Semicolon-joined inside a quoted cell (because of the comma in the joined value
        # only? No — semicolon doesn't trigger quoting; spaces don't either.
        # Actually "ISO 27001; SOC 2; GDPR" has no quoting trigger so it appears bare.
        assert "ISO 27001; SOC 2; GDPR" in csv

    def test_optional_uptime_renders_empty(self):
        services = [
            {
                "id": "svc-1",
                "componentName": "X",
                "description": "y",
                "provider": "Azure",
                "serviceType": "Cloud Infrastructure",
                "region": "x",
                "status": "Active",
                "securityCertifications": [],
                "tags": [],
            },
        ]
        csv = export_technology_services_csv(services)
        # No uptimePct → empty cell where it would appear
        # Header is at col index 7 (0-indexed): Uptime Pct
        # Just verify the row doesn't contain a stray decimal
        assert "99." not in csv


# ─── 07 Data Stores ──────────────────────────────────────────────────


class TestDataStores:
    def test_data_owner_resolved(self):
        stores = [
            {
                "id": "ds-1",
                "componentName": "Customer DB",
                "description": "main customer data",
                "storeType": "Relational Database",
                "dataClassification": "confidential",
                "location": "Azure",
                "gdprCompliant": True,
                "dataOwnerId": "p1",
                "tags": [],
            },
        ]
        persons = [{"id": "p1", "componentName": "Bob"}]
        csv = export_data_stores_csv(stores, persons)
        assert "Bob" in csv
        # GDPR boolean rendered as "true"
        assert "true,Bob" in csv

    def test_gdpr_boolean_rendering(self):
        compliant = export_data_stores_csv(
            [
                {
                    "id": "ds-1",
                    "componentName": "X",
                    "description": "y",
                    "storeType": "x",
                    "dataClassification": "x",
                    "location": "x",
                    "gdprCompliant": True,
                    "tags": [],
                }
            ],
            [],
        )
        not_compliant = export_data_stores_csv(
            [
                {
                    "id": "ds-2",
                    "componentName": "X",
                    "description": "y",
                    "storeType": "x",
                    "dataClassification": "x",
                    "location": "x",
                    "gdprCompliant": False,
                    "tags": [],
                }
            ],
            [],
        )
        assert "true," in compliant
        assert "false," in not_compliant


# ─── 08 Compliance Assessments ───────────────────────────────────────


class TestComplianceAssessments:
    def test_subject_and_approver_resolved(self):
        assessments = [
            {
                "id": "ca-1",
                "componentName": "EU AI Act assessment",
                "description": "assessment of x",
                "assessmentType": "EU AI Act",
                "subjectApplicationId": "app-1",
                "pass": True,
                "rationale": "all checks passed",
                "reviewDate": "2026-01-01",
                "approvalStatus": "Approved",
                "approvedByPersonId": "p1",
                "euAIActRiskLevel": "High-Risk",
                "tags": [],
            },
        ]
        apps = [{"id": "app-1", "componentName": "Contract Bot"}]
        persons = [{"id": "p1", "componentName": "DPO Alice"}]

        csv = export_compliance_assessments_csv(assessments, apps, persons)
        assert "Contract Bot" in csv
        assert "DPO Alice" in csv
        assert "High-Risk" in csv

    def test_subject_falls_back_to_id_when_app_not_found(self):
        assessments = [
            {
                "id": "ca-1",
                "componentName": "x",
                "description": "y",
                "assessmentType": "GDPR",
                "subjectApplicationId": "app-orphan",
                "pass": False,
                "rationale": "",
                "reviewDate": "",
                "approvalStatus": "Under Review",
                "tags": [],
            },
        ]
        csv = export_compliance_assessments_csv(assessments, [], [])
        # Orphan FK shows the raw ID rather than empty (so admins can see it)
        assert "app-orphan" in csv


# ─── 09 References ───────────────────────────────────────────────────


class TestReferences:
    def test_renders_edges(self):
        refs = [
            {
                "id": "ref-1",
                "sourceId": "app-1",
                "targetId": "person-1",
                "type": "Is Owner Of",
                "description": "Sarah owns Legal Bot",
            },
        ]
        csv = export_references_csv(refs)
        assert csv.startswith("Source Custom ID,Target Custom ID,Reference Type,Description\r\n")
        assert "app-1,person-1,Is Owner Of,Sarah owns Legal Bot" in csv


# ─── Bundle assembly ─────────────────────────────────────────────────


class TestBundle:
    def test_returns_9_files_in_canonical_order(self):
        bundle = export_ardoq_bundle({})
        names = [f["name"] for f in bundle["files"]]
        assert names == [
            "01_technical_capabilities.csv",
            "02_applications.csv",
            "03_technology_products.csv",
            "04_technology_services.csv",
            "05_people.csv",
            "06_organization.csv",
            "07_data_stores.csv",
            "08_compliance_assessments.csv",
            "09_references.csv",
        ]

    def test_empty_entities_produce_header_only_csvs(self):
        bundle = export_ardoq_bundle({})
        for f in bundle["files"]:
            # Each file: one header line + trailing CRLF
            assert f["csv"].endswith("\r\n")
            assert f["csv"].count("\r\n") == 1

    def test_totals_match_input_lengths(self):
        bundle = export_ardoq_bundle(
            {
                "applications": [{"id": f"app-{i}"} for i in range(5)],
                "persons": [{"id": "p1"}],
                "organizational_units": [{"id": "o1"}, {"id": "o2"}],
                "references": [{"id": f"ref-{i}"} for i in range(7)],
            },
        )
        assert bundle["totals"]["applications"] == 5
        assert bundle["totals"]["people"] == 1
        assert bundle["totals"]["organizations"] == 2
        assert bundle["totals"]["references"] == 7
        # Unspecified entities default to 0
        assert bundle["totals"]["data_stores"] == 0

    def test_generated_at_is_iso_timezone_aware(self):
        bundle = export_ardoq_bundle({})
        # Should end with +00:00 (UTC) — full ISO format
        assert "+00:00" in bundle["generated_at"] or bundle["generated_at"].endswith("Z")

    def test_fk_resolution_works_through_bundle(self):
        bundle = export_ardoq_bundle(
            {
                "applications": [
                    {
                        "id": "app-1",
                        "componentName": "Legal Bot",
                        "description": "x",
                        "ownerPersonId": "p1",
                        "deploymentStatus": "Active",
                        "riskScore": 0,
                        "tags": [],
                    },
                ],
                "persons": [{"id": "p1", "componentName": "Sarah"}],
            },
        )
        apps_csv = next(f["csv"] for f in bundle["files"] if f["name"] == "02_applications.csv")
        assert "Sarah" in apps_csv

    def test_idempotent_for_identical_input(self):
        """Same input → same output (modulo timestamp)."""
        entities = {
            "applications": [
                {
                    "id": "a1",
                    "componentName": "X",
                    "description": "y",
                    "deploymentStatus": "Active",
                    "riskScore": 0,
                    "tags": [],
                }
            ],
        }
        b1 = export_ardoq_bundle(entities)
        b2 = export_ardoq_bundle(entities)
        # Files identical
        assert b1["files"] == b2["files"]
        # Totals identical
        assert b1["totals"] == b2["totals"]


# ─── Empty input edge cases ──────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.parametrize(
        ("exporter", "args"),
        [
            (export_technical_capabilities_csv, ([],)),
            (export_applications_csv, ([], [], [])),
            (export_technology_products_csv, ([],)),
            (export_technology_services_csv, ([],)),
            (export_people_csv, ([],)),
            (export_organizations_csv, ([],)),
            (export_data_stores_csv, ([], [])),
            (export_compliance_assessments_csv, ([], [], [])),
            (export_references_csv, ([],)),
        ],
    )
    def test_empty_input_returns_header_only(self, exporter, args):
        csv = exporter(*args)
        # Single line + trailing CRLF
        assert csv.count("\r\n") == 1
        assert csv.endswith("\r\n")
