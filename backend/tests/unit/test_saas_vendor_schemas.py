"""SP-1 · SaaS Vendor AI Pydantic schemas — validation (DB-free).

Covers the wire-format schemas from ticket #190: vendor read/write,
assessment request/import, and the KPI aggregate. Happy + edge paths.
"""

from datetime import UTC

import pytest
from pydantic import ValidationError

from app.models.saas_vendor import (
    AssessmentImportSource,
    AssessmentStatus,
    VendorCategory,
    VendorDiscoveryMethod,
)
from app.schemas.saas_vendor import (
    AssessmentImportCreate,
    AssessmentImportResponse,
    AssessmentRequestCreate,
    AssessmentRequestResponse,
    SaaSVendorCreate,
    SaaSVendorResponse,
    SaaSVendorUpdate,
    VendorDataFlow,
    VendorKpiResponse,
)


def _valid_vendor_payload() -> dict:
    return {
        "name": "OpenAI",
        "department": "Engineering",
        "category": "llm_provider",
        "ai_feature": "ChatGPT Enterprise",
        "risk_score": 72,
        "data_flows": [
            {
                "description": "Prompts & uploaded files",
                "classification": "confidential",
                "trains_on_data": False,
            }
        ],
    }


# ── SaaSVendorCreate ──────────────────────────────────────────────────


def test_vendor_create_happy_path():
    v = SaaSVendorCreate(**_valid_vendor_payload())
    assert v.name == "OpenAI"
    assert v.category is VendorCategory.llm_provider
    assert v.risk_score == 72
    assert v.data_flows[0].trains_on_data is False
    # sensible defaults for the optional profile fields
    assert v.dpa is False
    assert v.discovered_via is VendorDiscoveryMethod.manual
    assert v.models == []
    assert v.certifications == []


def test_vendor_create_requires_name():
    payload = _valid_vendor_payload()
    del payload["name"]
    with pytest.raises(ValidationError):
        SaaSVendorCreate(**payload)


def test_vendor_create_rejects_blank_name():
    payload = _valid_vendor_payload()
    payload["name"] = ""
    with pytest.raises(ValidationError):
        SaaSVendorCreate(**payload)


def test_vendor_create_rejects_unknown_category():
    payload = _valid_vendor_payload()
    payload["category"] = "bogus"
    with pytest.raises(ValidationError):
        SaaSVendorCreate(**payload)


@pytest.mark.parametrize("score", [0, 100])
def test_risk_score_boundaries_ok(score):
    payload = _valid_vendor_payload()
    payload["risk_score"] = score
    assert SaaSVendorCreate(**payload).risk_score == score


@pytest.mark.parametrize("score", [-1, 101])
def test_risk_score_out_of_range_rejected(score):
    payload = _valid_vendor_payload()
    payload["risk_score"] = score
    with pytest.raises(ValidationError):
        SaaSVendorCreate(**payload)


# ── VendorDataFlow ────────────────────────────────────────────────────


def test_data_flow_requires_description_and_classification():
    with pytest.raises(ValidationError):
        VendorDataFlow(trains_on_data=True)


def test_data_flow_trains_defaults_false():
    f = VendorDataFlow(description="Support tickets", classification="internal")
    assert f.trains_on_data is False


# ── SaaSVendorUpdate (partial) ────────────────────────────────────────


def test_vendor_update_is_partial():
    u = SaaSVendorUpdate(risk_score=10)
    assert u.risk_score == 10
    assert u.name is None
    assert u.category is None


def test_vendor_update_still_range_checks_provided_fields():
    with pytest.raises(ValidationError):
        SaaSVendorUpdate(risk_score=200)


# ── SaaSVendorResponse ────────────────────────────────────────────────


def test_vendor_response_carries_provenance_and_owner():
    resp = SaaSVendorResponse(
        id="uc-1",
        name="OpenAI",
        department="Engineering",
        category="llm_provider",
        source="SHADOW",
        owner_user_id="user-9",
        risk_score=72,
        has_assessment=True,
    )
    assert resp.id == "uc-1"
    assert resp.source == "SHADOW"
    assert resp.owner_user_id == "user-9"
    assert resp.has_assessment is True
    # unset optional profile fields still default cleanly
    assert resp.models == []


# ── VendorKpiResponse ─────────────────────────────────────────────────


def test_kpi_response_fields():
    k = VendorKpiResponse(total=9, high_risk=3, trains_on_data=1, no_dpa=2)
    assert (k.total, k.high_risk, k.trains_on_data, k.no_dpa) == (9, 3, 1, 2)


# ── Assessment request / import ───────────────────────────────────────


def test_assessment_request_create_defaults():
    r = AssessmentRequestCreate()
    assert r.questionnaire == ""
    assert r.notes is None


def test_assessment_request_response_status_enum():
    r = AssessmentRequestResponse(
        id="a1",
        use_case_id="uc-1",
        questionnaire="SIG-Lite",
        status="sent",
    )
    assert r.status is AssessmentStatus.sent


def test_assessment_import_create_source_enum():
    imp = AssessmentImportCreate(source="trust_center", reference="https://trust.openai.com")
    assert imp.source is AssessmentImportSource.trust_center


def test_assessment_import_create_rejects_bad_source():
    with pytest.raises(ValidationError):
        AssessmentImportCreate(source="carrier_pigeon")


def test_assessment_import_response_shape():
    from datetime import datetime

    resp = AssessmentImportResponse(
        id="i1",
        use_case_id="uc-1",
        source="peer_shared",
        reference="acme-corp",
        payload={"summary": "SOC2 attached"},
        imported_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    assert resp.source is AssessmentImportSource.peer_shared
    assert resp.payload["summary"] == "SOC2 attached"
