"""SP-1 · SaaS Vendor AI models — metadata + registration (DB-free)."""

from app.models.saas_vendor import (
    AssessmentImportSource,
    AssessmentStatus,
    SaaSVendorProfile,
    VendorAssessmentImport,
    VendorAssessmentRequest,
    VendorCategory,
    VendorContractStatus,
    VendorDiscoveryMethod,
)


def test_tables_and_schema():
    assert SaaSVendorProfile.__tablename__ == "saas_vendor_profiles"
    assert VendorAssessmentRequest.__tablename__ == "vendor_assessment_requests"
    assert VendorAssessmentImport.__tablename__ == "vendor_assessment_imports"
    for m in (SaaSVendorProfile, VendorAssessmentRequest, VendorAssessmentImport):
        assert m.__table__.schema == "grc"
        # every table is tenant-scoped for RLS
        assert "tenant_id" in {c.name for c in m.__table__.columns}
        assert "use_case_id" in {c.name for c in m.__table__.columns}


def test_profile_columns_and_constraints():
    cols = {c.name for c in SaaSVendorProfile.__table__.columns}
    assert cols >= {
        "tenant_id",
        "use_case_id",
        "category",
        "discovered_via",
        "risk_score",
        "models",
        "sub_processors",
        "data_flows",
        "certifications",
        "compliance_status",
        "dpa",
        "training_opt_out",
        "contract_status",
        "last_reviewed_at",
    }
    constraint_names = {c.name for c in SaaSVendorProfile.__table__.constraints}
    # names carry the metadata naming_convention prefix; match the meaningful part
    assert any(n.startswith("uq") and "use_case" in n for n in constraint_names)  # 1:1
    assert any(n.startswith("ck") and "risk_score_range" in n for n in constraint_names)  # 0..100


def test_enum_values_match_migration():
    assert VendorCategory.llm_provider.value == "llm_provider"
    assert VendorDiscoveryMethod.browser_extension.value == "browser_extension"
    assert VendorContractStatus.pending_renewal.value == "pending_renewal"
    assert AssessmentStatus.reviewed.value == "reviewed"
    assert AssessmentImportSource.trust_center.value == "trust_center"


def test_use_case_relationship_and_exports():
    from app import models
    from app.models.use_case import UseCase

    assert models.SaaSVendorProfile is SaaSVendorProfile
    # 1:1 relationship wired both ways
    assert "saas_vendor_profile" in UseCase.__mapper__.relationships
    assert UseCase.__mapper__.relationships["saas_vendor_profile"].uselist is False
    assert "use_case" in SaaSVendorProfile.__mapper__.relationships
