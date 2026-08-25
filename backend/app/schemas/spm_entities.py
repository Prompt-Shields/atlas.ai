"""Pydantic schemas for the AI-SPM entity graph (M1 of epic #134).

Wire format for the seven entities added in migration 029. Shape follows the
028 schemas (``ai_asset.py``): a ``*Create`` / ``*Update`` / ``*Response`` /
``*ListResponse`` quad per entity, ``from_attributes`` on responses.

Two deliberate differences from ``ai_asset.py``:

  - Constrained vocabularies are ``Literal`` unions rather than bare ``str``.
    The DB keeps them as commented ``String`` columns (editable without a
    migration), but the wire layer rejects typos before they land.
  - Identifiers are typed ``uuid.UUID`` rather than ``str`` — they round-trip
    cleanly from ORM attributes and still serialise to strings in JSON.

Routers that consume these arrive in M2.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Shared vocabularies ─────────────────────────────────────────────

DataClassification = Literal["public", "internal", "confidential", "restricted"]
StoreType = Literal[
    "relational_database",
    "document_store",
    "object_store",
    "data_warehouse",
    "cache",
    "search_index",
    "other",
]
ServiceType = Literal[
    "cloud_infrastructure",
    "ai_platform",
    "api_gateway",
    "identity_provider",
    "database_service",
    "other",
]
ServiceStatus = Literal["active", "deprecated"]
ModelCategory = Literal[
    "llm", "computer_vision", "speech", "image_generation", "machine_learning", "other"
]
LifecycleStatus = Literal["production", "evaluation", "deprecated"]
EntityType = Literal[
    "person",
    "organizational_unit",
    "ai_asset",
    "data_store",
    "technology_service",
    "technology_product",
    "technical_capability",
    "compliance_assessment",
]
ReferenceType = Literal[
    "is_realized_by",
    "deploys",
    "is_owner_of",
    "observed_use",
    "belongs_to",
    "reads_from",
    "runs_on",
    "has_subject",
]


class _ResponseBase(BaseModel):
    """Common envelope fields on every entity response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    is_test_data: bool


# ─── Organizational unit ─────────────────────────────────────────────


class OrganizationalUnitCreate(BaseModel):
    component_name: str = Field(max_length=255)
    description: str | None = None
    external_id: str | None = Field(default=None, max_length=255)
    parent_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class OrganizationalUnitUpdate(BaseModel):
    component_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    external_id: str | None = Field(default=None, max_length=255)
    parent_id: uuid.UUID | None = None
    tags: list[str] | None = None


class OrganizationalUnitResponse(_ResponseBase):
    component_name: str
    description: str | None
    external_id: str | None
    parent_id: uuid.UUID | None
    tags: list[str]


class OrganizationalUnitListResponse(BaseModel):
    organizational_units: list[OrganizationalUnitResponse]
    total: int
    page: int
    page_size: int


# ─── Person ──────────────────────────────────────────────────────────


class PersonCreate(BaseModel):
    component_name: str = Field(max_length=255)
    description: str | None = None
    email: str | None = Field(default=None, max_length=320)
    role: str | None = Field(default=None, max_length=255)
    external_id: str | None = Field(default=None, max_length=255)
    organizational_unit_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class PersonUpdate(BaseModel):
    component_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    email: str | None = Field(default=None, max_length=320)
    role: str | None = Field(default=None, max_length=255)
    external_id: str | None = Field(default=None, max_length=255)
    organizational_unit_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    tags: list[str] | None = None


class PersonResponse(_ResponseBase):
    component_name: str
    description: str | None
    email: str | None
    role: str | None
    external_id: str | None
    organizational_unit_id: uuid.UUID | None
    user_id: uuid.UUID | None
    tags: list[str]


class PersonListResponse(BaseModel):
    people: list[PersonResponse]
    total: int
    page: int
    page_size: int


# ─── Data store ──────────────────────────────────────────────────────


class DataStoreCreate(BaseModel):
    component_name: str = Field(max_length=255)
    description: str | None = None
    store_type: StoreType = "other"
    data_classification: DataClassification = "internal"
    location: str | None = Field(default=None, max_length=255)
    record_count: str | None = Field(default=None, max_length=100)
    refresh_frequency: str | None = Field(default=None, max_length=100)
    retention_period: str | None = Field(default=None, max_length=100)
    encryption: str | None = Field(default=None, max_length=255)
    access_controls: str | None = None
    gdpr_compliant: bool = False
    last_audit: date | None = None
    data_owner_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class DataStoreUpdate(BaseModel):
    component_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    store_type: StoreType | None = None
    data_classification: DataClassification | None = None
    location: str | None = Field(default=None, max_length=255)
    record_count: str | None = Field(default=None, max_length=100)
    refresh_frequency: str | None = Field(default=None, max_length=100)
    retention_period: str | None = Field(default=None, max_length=100)
    encryption: str | None = Field(default=None, max_length=255)
    access_controls: str | None = None
    gdpr_compliant: bool | None = None
    last_audit: date | None = None
    data_owner_id: uuid.UUID | None = None
    tags: list[str] | None = None


class DataStoreResponse(_ResponseBase):
    component_name: str
    description: str | None
    store_type: str
    data_classification: str
    location: str | None
    record_count: str | None
    refresh_frequency: str | None
    retention_period: str | None
    encryption: str | None
    access_controls: str | None
    gdpr_compliant: bool
    last_audit: date | None
    data_owner_id: uuid.UUID | None
    tags: list[str]


class DataStoreListResponse(BaseModel):
    data_stores: list[DataStoreResponse]
    total: int
    page: int
    page_size: int


# ─── Technology service ──────────────────────────────────────────────


class TechnologyServiceCreate(BaseModel):
    component_name: str = Field(max_length=255)
    description: str | None = None
    provider: str | None = Field(default=None, max_length=255)
    service_type: ServiceType = "other"
    region: str | None = Field(default=None, max_length=100)
    status: ServiceStatus = "active"
    uptime_pct: float | None = Field(default=None, ge=0, le=100)
    cost_per_month_nok: float | None = Field(default=None, ge=0)
    security_certifications: list[str] = Field(default_factory=list)
    network_isolation: str | None = Field(default=None, max_length=255)
    scaling_policy: str | None = Field(default=None, max_length=255)
    backup_strategy: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)


class TechnologyServiceUpdate(BaseModel):
    component_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    provider: str | None = Field(default=None, max_length=255)
    service_type: ServiceType | None = None
    region: str | None = Field(default=None, max_length=100)
    status: ServiceStatus | None = None
    uptime_pct: float | None = Field(default=None, ge=0, le=100)
    cost_per_month_nok: float | None = Field(default=None, ge=0)
    security_certifications: list[str] | None = None
    network_isolation: str | None = Field(default=None, max_length=255)
    scaling_policy: str | None = Field(default=None, max_length=255)
    backup_strategy: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = None


class TechnologyServiceResponse(_ResponseBase):
    component_name: str
    description: str | None
    provider: str | None
    service_type: str
    region: str | None
    status: str
    uptime_pct: float | None
    cost_per_month_nok: float | None
    security_certifications: list[str]
    network_isolation: str | None
    scaling_policy: str | None
    backup_strategy: str | None
    tags: list[str]


class TechnologyServiceListResponse(BaseModel):
    technology_services: list[TechnologyServiceResponse]
    total: int
    page: int
    page_size: int


# ─── Technology product ──────────────────────────────────────────────


class TechnologyProductCreate(BaseModel):
    component_name: str = Field(max_length=255)
    description: str | None = None
    provider: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=100)
    model_category: ModelCategory = "other"
    parameters: str | None = Field(default=None, max_length=100)
    lifecycle_status: LifecycleStatus = "production"
    input_cost_per_mtokens: str | None = Field(default=None, max_length=50)
    output_cost_per_mtokens: str | None = Field(default=None, max_length=50)
    context_window: str | None = Field(default=None, max_length=50)
    estimated_monthly_spend_nok: float | None = Field(default=None, ge=0)
    promptly_app_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TechnologyProductUpdate(BaseModel):
    component_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    provider: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=100)
    model_category: ModelCategory | None = None
    parameters: str | None = Field(default=None, max_length=100)
    lifecycle_status: LifecycleStatus | None = None
    input_cost_per_mtokens: str | None = Field(default=None, max_length=50)
    output_cost_per_mtokens: str | None = Field(default=None, max_length=50)
    context_window: str | None = Field(default=None, max_length=50)
    estimated_monthly_spend_nok: float | None = Field(default=None, ge=0)
    promptly_app_ids: list[str] | None = None
    tags: list[str] | None = None


class TechnologyProductResponse(_ResponseBase):
    component_name: str
    description: str | None
    provider: str | None
    version: str | None
    model_category: str
    parameters: str | None
    lifecycle_status: str
    input_cost_per_mtokens: str | None
    output_cost_per_mtokens: str | None
    context_window: str | None
    estimated_monthly_spend_nok: float | None
    promptly_app_ids: list[str]
    tags: list[str]


class TechnologyProductListResponse(BaseModel):
    technology_products: list[TechnologyProductResponse]
    total: int
    page: int
    page_size: int


# ─── Technical capability ────────────────────────────────────────────


class TechnicalCapabilityCreate(BaseModel):
    level1: str = Field(max_length=255)
    level2: str | None = Field(default=None, max_length=255)
    level3: str | None = Field(default=None, max_length=255)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class TechnicalCapabilityUpdate(BaseModel):
    level1: str | None = Field(default=None, max_length=255)
    level2: str | None = Field(default=None, max_length=255)
    level3: str | None = Field(default=None, max_length=255)
    description: str | None = None
    tags: list[str] | None = None


class TechnicalCapabilityResponse(_ResponseBase):
    level1: str
    level2: str | None
    level3: str | None
    description: str | None
    tags: list[str]


class TechnicalCapabilityListResponse(BaseModel):
    technical_capabilities: list[TechnicalCapabilityResponse]
    total: int
    page: int
    page_size: int


# ─── Entity reference (edge) ─────────────────────────────────────────


class EntityReferenceCreate(BaseModel):
    source_id: uuid.UUID
    source_type: EntityType
    target_id: uuid.UUID
    target_type: EntityType
    reference_type: ReferenceType
    description: str | None = None
    auto_derived: bool = False


class EntityReferenceUpdate(BaseModel):
    """Edges are append-only; only the annotation fields are mutable."""

    description: str | None = None
    auto_derived: bool | None = None


class EntityReferenceResponse(_ResponseBase):
    source_id: uuid.UUID
    source_type: str
    target_id: uuid.UUID
    target_type: str
    reference_type: str
    description: str | None
    auto_derived: bool
    observation_count: int


class EntityReferenceListResponse(BaseModel):
    references: list[EntityReferenceResponse]
    total: int
    page: int
    page_size: int
