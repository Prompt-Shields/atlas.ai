"""Pydantic schemas for the SaaS Vendor AI router (SP-1, ticket #190).

Wire format for third-party / supply-chain AI vendors. A vendor is a
``use_cases`` row carrying a 1:1 ``saas_vendor_profiles`` row, so the response
flattens the reused use-case fields (name/title, department, owner, provenance)
together with the vendor-only profile fields. See ``app.models.saas_vendor``
and epic #187.

Enum *values* mirror the ORM enums (snake_case), so the wire contract stays in
lockstep with the DB. Risk scores are range-checked (0-100) to match the
``risk_score_range`` CHECK constraint on the profile table.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.saas_vendor import (
    AssessmentImportSource,
    AssessmentStatus,
    VendorCategory,
    VendorContractStatus,
    VendorDiscoveryMethod,
)


class VendorDataFlow(BaseModel):
    """One data flow the vendor's AI feature touches."""

    description: str
    classification: str
    trains_on_data: bool = False


class _VendorProfileFields(BaseModel):
    """Vendor-only profile fields shared by create/response payloads."""

    category: VendorCategory
    ai_feature: str = ""  # maps to use_case.tool
    discovered_via: VendorDiscoveryMethod = VendorDiscoveryMethod.manual
    risk_score: int = Field(default=0, ge=0, le=100)
    models: list[str] = Field(default_factory=list)
    sub_processors: list[str] = Field(default_factory=list)
    data_flows: list[VendorDataFlow] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    compliance_status: dict[str, str] = Field(default_factory=dict)
    dpa: bool = False
    training_opt_out: bool = False
    contract_status: VendorContractStatus = VendorContractStatus.active
    last_reviewed_at: date | None = None


class SaaSVendorCreate(_VendorProfileFields):
    """Create a vendor: a new use-case row + its 1:1 profile."""

    name: str = Field(min_length=1)  # use_case.title
    department: str = Field(min_length=1)
    owner_user_id: str | None = None


class SaaSVendorUpdate(BaseModel):
    """Partial update — every field optional; provided fields still validate."""

    name: str | None = Field(default=None, min_length=1)
    department: str | None = Field(default=None, min_length=1)
    owner_user_id: str | None = None
    category: VendorCategory | None = None
    ai_feature: str | None = None
    discovered_via: VendorDiscoveryMethod | None = None
    risk_score: int | None = Field(default=None, ge=0, le=100)
    models: list[str] | None = None
    sub_processors: list[str] | None = None
    data_flows: list[VendorDataFlow] | None = None
    certifications: list[str] | None = None
    compliance_status: dict[str, str] | None = None
    dpa: bool | None = None
    training_opt_out: bool | None = None
    contract_status: VendorContractStatus | None = None
    last_reviewed_at: date | None = None


class SaaSVendorResponse(_VendorProfileFields):
    """A vendor as returned by the API: profile fields + reused use-case fields."""

    id: str  # use_case id
    name: str
    department: str
    owner_user_id: str | None = None
    # Provenance: the use_case.source value (e.g. "SHADOW", "MANUAL").
    source: str
    # Whether any assessment request/import exists (drives the provenance chip).
    has_assessment: bool = False

    class Config:
        from_attributes = True


class VendorKpiResponse(BaseModel):
    """Register KPI strip (ported from ai-spm-dashboard).

    ``high_risk`` counts risk_score >= 60; ``trains_on_data`` counts vendors
    with any data flow that trains on your data; ``no_dpa`` counts vendors
    without a DPA in place.
    """

    total: int
    high_risk: int
    trains_on_data: int
    no_dpa: int


class AssessmentRequestCreate(BaseModel):
    """Outbound 'request assessment' input (in-app simulated send)."""

    questionnaire: str = ""
    notes: str | None = None


class AssessmentRequestResponse(BaseModel):
    """A vendor assessment request row."""

    id: str
    use_case_id: str
    questionnaire: str
    status: AssessmentStatus
    token: str | None = None
    sent_at: datetime | None = None
    received_at: datetime | None = None
    notes: str | None = None

    class Config:
        from_attributes = True


class AssessmentImportCreate(BaseModel):
    """Inbound 'import assessment' input (trust-center / peer-shared / upload)."""

    source: AssessmentImportSource
    reference: str | None = None
    payload: dict = Field(default_factory=dict)


class AssessmentImportResponse(BaseModel):
    """A vendor assessment import row."""

    id: str
    use_case_id: str
    source: AssessmentImportSource
    reference: str | None = None
    payload: dict
    imported_at: datetime

    class Config:
        from_attributes = True
