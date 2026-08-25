"""Unit tests for Intune / Purview / Defender CASB normalise_* functions.

Pure-function tests on the raw Graph payload → ORM field dict
adapters. No DB, no HTTP.
"""

from __future__ import annotations

import pytest

from app.services.defender_sync import normalise_detection
from app.services.intune_sync import normalise_device
from app.services.purview_sync import normalise_event

pytestmark = [pytest.mark.unit]


# ─── Intune ──────────────────────────────────────────────────────────


class TestIntuneNormalise:
    def test_full_payload(self) -> None:
        raw = {
            "id": "abc-123",
            "deviceName": "  Surface-1  ",
            "operatingSystem": "Windows",
            "osVersion": "11.0",
            "complianceState": "compliant",
            "userPrincipalName": "Alice@Example.com",
            "lastSyncDateTime": "2026-05-01T12:00:00Z",
        }
        out = normalise_device(raw)
        assert out["external_device_id"] == "abc-123"
        assert out["device_name"] == "Surface-1"
        assert out["operating_system"] == "Windows"
        assert out["os_version"] == "11.0"
        assert out["compliance_state"] == "compliant"
        assert out["enrolled_user_email"] == "alice@example.com"
        assert out["last_sync_to_mdm_at"] is not None

    def test_missing_optional_fields(self) -> None:
        raw = {"id": "d1", "deviceName": "Device"}
        out = normalise_device(raw)
        assert out["operating_system"] is None
        assert out["enrolled_user_email"] is None
        assert out["last_sync_to_mdm_at"] is None

    def test_invalid_datetime_falls_back(self) -> None:
        raw = {"id": "d1", "deviceName": "X", "lastSyncDateTime": "not a date"}
        assert normalise_device(raw)["last_sync_to_mdm_at"] is None

    def test_unnamed_device(self) -> None:
        raw = {"id": "d1", "deviceName": ""}
        assert normalise_device(raw)["device_name"] == "(unnamed)"


# ─── Purview ─────────────────────────────────────────────────────────


class TestPurviewNormalise:
    def test_purview_alert(self) -> None:
        raw = {
            "id": "alert-1",
            "productName": "Microsoft Purview",
            "category": "dlp_match",
            "severity": "HIGH",
            "title": "SSN detected in OneDrive",
            "description": "Pattern match for SSN classifier",
            "createdDateTime": "2026-05-01T10:30:00Z",
            "classifierName": "us-ssn",
            "evidence": [
                {
                    "userAccount": {
                        "userPrincipalName": "bob@example.com",
                    },
                },
            ],
        }
        out = normalise_event(raw)
        assert out is not None
        assert out["external_event_id"] == "alert-1"
        assert out["event_type"] == "dlp_match"
        assert out["severity"] == "high"
        assert out["title"] == "SSN detected in OneDrive"
        assert out["classifier_id"] == "us-ssn"
        assert out["actor_user_principal_name"] == "bob@example.com"
        assert out["detected_at"] is not None

    def test_non_purview_returns_none(self) -> None:
        raw = {
            "id": "x",
            "productName": "Microsoft Sentinel",
            "createdDateTime": "2026-05-01T00:00:00Z",
        }
        assert normalise_event(raw) is None

    def test_missing_created_date_returns_none(self) -> None:
        raw = {"id": "x", "productName": "Microsoft Purview"}
        assert normalise_event(raw) is None

    def test_default_event_type(self) -> None:
        raw = {
            "id": "x",
            "productName": "Microsoft Purview",
            "createdDateTime": "2026-05-01T00:00:00Z",
            "title": "untyped",
        }
        out = normalise_event(raw)
        assert out is not None and out["event_type"] == "dlp_match"


# ─── Defender CASB ───────────────────────────────────────────────────


class TestDefenderNormalise:
    def test_full_payload(self) -> None:
        raw = {
            "id": "det-1",
            "productName": "Microsoft Defender for Cloud Apps",
            "category": "ShadowITDiscovery",
            "createdDateTime": "2026-05-01T00:00:00Z",
            "lastActivityDateTime": "2026-05-10T00:00:00Z",
            "firstActivityDateTime": "2026-04-01T00:00:00Z",
            "evidence": [
                {
                    "@odata.type": "#microsoft.graph.security.cloudApplicationEvidence",
                    "displayName": "ChatGPT",
                    "instanceName": "chatgpt.com",
                    "riskScore": 4,
                },
                {
                    "@odata.type": "#microsoft.graph.security.userEvidence",
                    "userAccount": {
                        "userPrincipalName": "alice@example.com",
                    },
                },
            ],
        }
        out = normalise_detection(raw)
        assert out is not None
        assert out["external_detection_id"] == "det-1"
        assert out["app_name"] == "ChatGPT"
        assert out["app_domain"] == "chatgpt.com"
        assert out["app_category"] == "ShadowITDiscovery"
        assert out["risk_score"] == 4
        assert out["user_principal_name"] == "alice@example.com"

    def test_non_casb_returns_none(self) -> None:
        raw = {
            "id": "x",
            "productName": "Microsoft Sentinel",
        }
        assert normalise_detection(raw) is None

    def test_no_app_evidence_returns_none(self) -> None:
        raw = {
            "id": "x",
            "productName": "Microsoft Defender for Cloud Apps",
            "evidence": [],
        }
        assert normalise_detection(raw) is None

    def test_no_product_name_accepted(self) -> None:
        """Alerts can omit productName entirely; we don't reject those."""
        raw = {
            "id": "x",
            "evidence": [
                {
                    "@odata.type": "#microsoft.graph.security.cloudApplicationEvidence",
                    "displayName": "Notion",
                    "instanceName": "notion.so",
                }
            ],
        }
        out = normalise_detection(raw)
        assert out is not None and out["app_name"] == "Notion"
