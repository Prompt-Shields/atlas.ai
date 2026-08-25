"""AI-SPM entity domain — the Ardoq-style component graph (M1 of epic #134).

Ports the seven remaining `lib/entities/types.ts` entities from
ai-spm-dashboard. Migration 028 already landed the inventory half
(ai_assets ≙ Application, compliance_assessments ≙ ComplianceAssessment);
this module adds the rest of the graph plus the edge table that links it.

Conventions follow 028 rather than the TypeScript source:

  - UUID primary keys via ``GRCBase``. The dashboard's kebab-case
    ``CustomId`` (``person-sarah-chen``) is dropped — it existed only so the
    in-memory ``Map`` stores had a stable key. External/import identity is
    carried by ``external_id`` where a caller needs to re-find a row.
  - ``OrgScopedMixin`` (tenant_id + org_id) so every table sits behind the
    same RLS policy as the inventory tables.
  - Free-text ``String`` columns with a ``comment`` listing the allowed
    values, instead of DB enums — matches ``ai_assets.deployment_status``
    and keeps the vocabulary editable without a migration.
  - List-valued fields (``tags``, ``security_certifications``,
    ``promptly_app_ids``) are JSONB on Postgres, JSON on SQLite (test suite).

``EntityReference`` is deliberately polymorphic: a reference can point at any
two entity kinds, so source/target are bare UUIDs with a type discriminator
rather than real FKs. Everything else uses real FKs.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, OrgScopedMixin, TestDataMixin

# JSONB on PostgreSQL (GIN-indexable), plain JSON on SQLite (test suite).
_JSONBColumn = JSON().with_variant(JSONB(), "postgresql")


class OrganizationalUnit(GRCBase, OrgScopedMixin, TestDataMixin):
    """A department / business unit. Self-nesting via ``parent_id``."""

    __tablename__ = "organizational_units"

    component_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.organizational_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tags: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)


class Person(GRCBase, OrgScopedMixin, TestDataMixin):
    """A human component in the graph — owner, operator, or observed user.

    Distinct from ``users`` (who can authenticate) and ``directory_users``
    (synced from Entra): a Person is a graph node that may have no login at
    all. ``user_id`` links to a real account when one exists.
    """

    __tablename__ = "people"

    component_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    organizational_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.organizational_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tags: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)


class DataStore(GRCBase, OrgScopedMixin, TestDataMixin):
    """A data repository an AI system reads from or writes to."""

    __tablename__ = "data_stores"

    component_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="other",
        comment=(
            "relational_database, document_store, object_store, data_warehouse, "
            "cache, search_index, other"
        ),
    )
    data_classification: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="internal",
        comment="public, internal, confidential, restricted",
    )
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    record_count: Mapped[str | None] = mapped_column(String(100), nullable=True)
    refresh_frequency: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retention_period: Mapped[str | None] = mapped_column(String(100), nullable=True)
    encryption: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_controls: Mapped[str | None] = mapped_column(Text, nullable=True)
    gdpr_compliant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_audit: Mapped[date | None] = mapped_column(Date, nullable=True)

    data_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grc.people.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tags: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)


class TechnologyService(GRCBase, OrgScopedMixin, TestDataMixin):
    """Infrastructure an AI system runs on (Azure OpenAI, an API gateway…)."""

    __tablename__ = "technology_services"

    component_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    service_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="other",
        comment=(
            "cloud_infrastructure, ai_platform, api_gateway, identity_provider, "
            "database_service, other"
        ),
    )
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        comment="active, deprecated",
    )
    uptime_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_per_month_nok: Mapped[float | None] = mapped_column(Float, nullable=True)
    security_certifications: Mapped[list[str]] = mapped_column(
        _JSONBColumn, nullable=False, default=list
    )
    network_isolation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scaling_policy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backup_strategy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)


class TechnologyProduct(GRCBase, OrgScopedMixin, TestDataMixin):
    """A concrete model / product (GPT-4o, Claude…) an application deploys."""

    __tablename__ = "technology_products"

    component_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="other",
        comment=("llm, computer_vision, speech, image_generation, machine_learning, other"),
    )
    parameters: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="production",
        comment="production, evaluation, deprecated",
    )
    input_cost_per_mtokens: Mapped[str | None] = mapped_column(String(50), nullable=True)
    output_cost_per_mtokens: Mapped[str | None] = mapped_column(String(50), nullable=True)
    context_window: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estimated_monthly_spend_nok: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Promptly hint: which MonitoredApp ids this product backs (e.g. ["chatgpt"]),
    # used to attribute observed usage to a concrete model.
    promptly_app_ids: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)


class TechnicalCapability(GRCBase, OrgScopedMixin, TestDataMixin):
    """Read-only seeded 3-level taxonomy ("Artificial Intelligence" → "LLM" → …).

    Required by the Ardoq AI Lens to recognise a component as an AI System, so
    ``level1`` must match the reference vocabulary exactly.
    """

    __tablename__ = "technical_capabilities"

    level1: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    level2: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    level3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)


class EntityReference(GRCBase, OrgScopedMixin, TestDataMixin):
    """A typed, directed edge between two entities — append-only.

    Polymorphic by design: ``source_id``/``target_id`` are bare UUIDs plus a
    ``*_type`` discriminator, because an edge may join any two entity kinds
    (Person→Application, Application→DataStore, …). No FK constraint is
    possible across a polymorphic pair, so referential integrity is enforced
    at the service layer.

    ``auto_derived`` marks edges materialised from telemetry (Observed Use,
    Belongs To). Those may be recomputed on a schedule; manual edges stick.
    """

    __tablename__ = "entity_references"
    __table_args__ = (
        # An edge is unique by (source, target, type) within a tenant —
        # re-observing one bumps observation_count instead of duplicating.
        # Migration 029 declares this exact name so create_all (tests) and
        # the migration (prod) agree.
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "target_id",
            "reference_type",
            name="uq_entity_reference_edge",
        ),
        {"schema": "grc"},
    )

    source_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "person, organizational_unit, ai_asset, data_store, technology_service, "
            "technology_product, technical_capability, compliance_assessment"
        ),
    )
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment=(
            "is_realized_by, deploys, is_owner_of, observed_use, belongs_to, "
            "reads_from, runs_on, has_subject"
        ),
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # How many times telemetry has re-observed this edge; only meaningful for
    # auto-derived edges, where it doubles as a confidence signal.
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
