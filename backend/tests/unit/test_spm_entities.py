"""Tests for the AI-SPM entity graph (M1, issue #135).

Covers the seven entities added in migration 029: persistence + defaults,
the FK wiring between them, self-nesting org units, the append-only edge
constraint, and the Literal vocabularies on the wire schemas.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.spm_entities import (
    DataStore,
    EntityReference,
    OrganizationalUnit,
    Person,
    TechnicalCapability,
    TechnologyProduct,
    TechnologyService,
)
from app.schemas.spm_entities import (
    DataStoreCreate,
    EntityReferenceCreate,
    PersonCreate,
    TechnologyProductCreate,
    TechnologyServiceCreate,
)
from tests.conftest import TEST_ORG_ID, TEST_TENANT_ID, TestSessionLocal

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _scope() -> dict[str, uuid.UUID]:
    return {"tenant_id": TEST_TENANT_ID, "org_id": TEST_ORG_ID}


# ─── Persistence + defaults ──────────────────────────────────────────


async def test_all_seven_entities_persist_with_defaults() -> None:
    async with TestSessionLocal() as db:
        ou = OrganizationalUnit(**_scope(), component_name="Legal")
        db.add(ou)
        await db.flush()

        person = Person(
            **_scope(),
            component_name="Sarah Chen",
            email="sarah@example.test",
            role="Counsel",
            organizational_unit_id=ou.id,
        )
        db.add(person)
        await db.flush()

        db.add_all(
            [
                DataStore(**_scope(), component_name="CRM", data_owner_id=person.id),
                TechnologyService(**_scope(), component_name="Azure OpenAI"),
                TechnologyProduct(**_scope(), component_name="GPT-4o"),
                TechnicalCapability(**_scope(), level1="Artificial Intelligence"),
            ]
        )
        await db.flush()

        store = (await db.execute(select(DataStore))).scalar_one()
        service = (await db.execute(select(TechnologyService))).scalar_one()
        product = (await db.execute(select(TechnologyProduct))).scalar_one()

        # Column defaults land without being passed explicitly.
        assert store.store_type == "other"
        assert store.data_classification == "internal"
        assert store.gdpr_compliant is False
        assert store.data_owner_id == person.id
        assert service.service_type == "other"
        assert service.status == "active"
        assert product.model_category == "other"
        assert product.lifecycle_status == "production"
        # JSON list columns default to [] rather than NULL.
        assert product.promptly_app_ids == []
        assert product.tags == []
        assert service.security_certifications == []


async def test_org_unit_self_nesting() -> None:
    async with TestSessionLocal() as db:
        parent = OrganizationalUnit(**_scope(), component_name="Engineering")
        db.add(parent)
        await db.flush()

        child = OrganizationalUnit(**_scope(), component_name="Platform", parent_id=parent.id)
        db.add(child)
        await db.flush()

        assert child.parent_id == parent.id
        roots = (
            (
                await db.execute(
                    select(OrganizationalUnit).where(OrganizationalUnit.parent_id.is_(None))
                )
            )
            .scalars()
            .all()
        )
        assert [r.component_name for r in roots] == ["Engineering"]


# ─── Edges ───────────────────────────────────────────────────────────


async def test_entity_reference_edge_is_unique_per_tenant() -> None:
    async with TestSessionLocal() as db:
        source_id, target_id = uuid.uuid4(), uuid.uuid4()

        def _edge() -> EntityReference:
            return EntityReference(
                **_scope(),
                source_id=source_id,
                source_type="person",
                target_id=target_id,
                target_type="ai_asset",
                reference_type="observed_use",
            )

        db.add(_edge())
        await db.flush()

        # Re-observing the same edge must not insert a duplicate.
        db.add(_edge())
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_entity_reference_defaults() -> None:
    async with TestSessionLocal() as db:
        edge = EntityReference(
            **_scope(),
            source_id=uuid.uuid4(),
            source_type="person",
            target_id=uuid.uuid4(),
            target_type="organizational_unit",
            reference_type="belongs_to",
        )
        db.add(edge)
        await db.flush()

        assert edge.auto_derived is False
        assert edge.observation_count == 0


# ─── Wire schema vocabularies ────────────────────────────────────────


async def test_schema_defaults_match_column_defaults() -> None:
    store = DataStoreCreate(component_name="CRM")
    assert store.store_type == "other"
    assert store.data_classification == "internal"
    assert store.tags == []

    service = TechnologyServiceCreate(component_name="Azure OpenAI")
    assert service.service_type == "other"
    assert service.status == "active"

    product = TechnologyProductCreate(component_name="GPT-4o")
    assert product.model_category == "other"
    assert product.lifecycle_status == "production"


@pytest.mark.parametrize(
    ("factory", "field", "bad"),
    [
        (DataStoreCreate, "store_type", "Relational Database"),  # TS-cased, rejected
        (DataStoreCreate, "data_classification", "secret"),
        (TechnologyServiceCreate, "service_type", "AI Platform"),
        (TechnologyServiceCreate, "status", "retired"),
        (TechnologyProductCreate, "model_category", "LLM"),
        (TechnologyProductCreate, "lifecycle_status", "sunset"),
    ],
)
async def test_invalid_vocabulary_values_rejected(factory: type, field: str, bad: str) -> None:
    with pytest.raises(ValidationError):
        factory(component_name="x", **{field: bad})


async def test_reference_type_and_entity_type_validated() -> None:
    ok = EntityReferenceCreate(
        source_id=uuid.uuid4(),
        source_type="person",
        target_id=uuid.uuid4(),
        target_type="ai_asset",
        reference_type="is_owner_of",
    )
    assert ok.auto_derived is False

    with pytest.raises(ValidationError):
        EntityReferenceCreate(
            source_id=uuid.uuid4(),
            source_type="person",
            target_id=uuid.uuid4(),
            target_type="ai_asset",
            reference_type="Is Owner Of",  # TS-cased, rejected
        )


async def test_uptime_pct_range_enforced() -> None:
    assert TechnologyServiceCreate(component_name="s", uptime_pct=99.9).uptime_pct == 99.9
    with pytest.raises(ValidationError):
        TechnologyServiceCreate(component_name="s", uptime_pct=101)


async def test_person_email_and_optional_fields() -> None:
    p = PersonCreate(component_name="Sarah Chen")
    assert p.email is None
    assert p.tags == []
