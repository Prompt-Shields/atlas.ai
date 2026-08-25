"""Unit tests for /api/v1/use-cases.

Covers:
  - CRUD lifecycle (create → list → detail → patch → delete)
  - Tenant isolation (Tenant A can't read Tenant B's records)
  - RBAC (Viewer can read; OrgAdmin+ can write)
  - Status filter + counts endpoint
  - Soft delete moves status to RETIRED (not row-delete)
  - data_classes JSON roundtrip via the str-as-JSON column
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.auth.jwt import create_access_token
from tests.conftest import (
    TEST_TENANT_ID,
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


SECOND_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")
SECOND_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")


def _second_tenant_admin_token() -> str:
    """JWT for an OrgAdmin on a *different* tenant — for isolation tests."""
    return create_access_token(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        email="other@test.local",
        roles=["ORG_ADMIN"],
        tenant_id=SECOND_TENANT_ID,
        org_id=SECOND_ORG_ID,
    )


def _valid_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Grant memo drafting",
        "tool": "Microsoft Copilot",
        "department": "Grants Management",
        "source": "FORM",
        "data_classes": ["grantee_metadata"],
        "frequency": "weekly",
    }
    base.update(overrides)
    return base


class TestCreate:
    async def test_org_admin_can_create(self, client: AsyncClient, tenant_admin_token: str) -> None:
        resp = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "Grant memo drafting"
        assert body["tool"] == "Microsoft Copilot"
        assert body["source"] == "FORM"
        assert body["status"] == "DRAFT"
        assert body["risk_tier"] == "LOW"
        assert body["data_classes"] == ["grantee_metadata"]
        assert body["tenant_id"] == str(TEST_TENANT_ID)
        assert body["id"]

    async def test_viewer_cannot_create(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(viewer_token),
            json=_valid_payload(),
        )
        assert resp.status_code == 403

    async def test_unauthenticated_rejected(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/use-cases", json=_valid_payload())
        assert resp.status_code == 401

    async def test_data_classes_are_normalised(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        """Trimmed, lowercased, deduplicated."""
        resp = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(data_classes=["  Customer_PII ", "customer_pii", "PHI", ""]),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data_classes"] == ["customer_pii", "phi"]

    async def test_missing_title_rejected(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        bad = _valid_payload()
        del bad["title"]
        resp = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=bad,
        )
        assert resp.status_code == 422

    async def test_invalid_source_rejected(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(source="NOT_REAL"),
        )
        assert resp.status_code == 422

    async def test_bot_source_with_response_id(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        """The bot in PR B will POST records with source=BOT + response_id."""
        response_id = "11111111-2222-3333-4444-555555555555"
        resp = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(
                source="BOT",
                dispatched_from_response_id=response_id,
            ),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["source"] == "BOT"
        assert body["dispatched_from_response_id"] == response_id


class TestList:
    async def test_empty_list(self, client: AsyncClient, tenant_admin_token: str) -> None:
        resp = await client.get("/api/v1/use-cases", headers=auth_header(tenant_admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["use_cases"] == []
        assert body["page"] == 1

    async def test_viewer_can_list(
        self, client: AsyncClient, tenant_admin_token: str, viewer_token: str
    ) -> None:
        await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        resp = await client.get("/api/v1/use-cases", headers=auth_header(viewer_token))
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_status_filter(self, client: AsyncClient, tenant_admin_token: str) -> None:
        # Two drafts + one active
        for title, status in [
            ("Draft 1", "DRAFT"),
            ("Draft 2", "DRAFT"),
            ("Active 1", "ACTIVE"),
        ]:
            await client.post(
                "/api/v1/use-cases",
                headers=auth_header(tenant_admin_token),
                json=_valid_payload(title=title, status=status),
            )

        resp = await client.get(
            "/api/v1/use-cases?status=DRAFT",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        for uc in body["use_cases"]:
            assert uc["status"] == "DRAFT"

    async def test_source_filter(self, client: AsyncClient, tenant_admin_token: str) -> None:
        for src in ["FORM", "BOT", "BOT", "SHADOW_PROMOTE"]:
            await client.post(
                "/api/v1/use-cases",
                headers=auth_header(tenant_admin_token),
                json=_valid_payload(source=src),
            )

        resp = await client.get(
            "/api/v1/use-cases?source=BOT",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.json()["total"] == 2


class TestCounts:
    async def test_counts_by_status(self, client: AsyncClient, tenant_admin_token: str) -> None:
        for title, status in [
            ("d1", "DRAFT"),
            ("d2", "DRAFT"),
            ("r1", "REVIEW"),
            ("a1", "ACTIVE"),
            ("a2", "ACTIVE"),
            ("a3", "ACTIVE"),
        ]:
            await client.post(
                "/api/v1/use-cases",
                headers=auth_header(tenant_admin_token),
                json=_valid_payload(title=title, status=status),
            )

        resp = await client.get(
            "/api/v1/use-cases/counts",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "total": 6,
            "draft": 2,
            "review": 1,
            "active": 3,
            "retired": 0,
        }


class TestDetail:
    async def test_get_by_id(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        uc_id = created.json()["id"]

        resp = await client.get(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == uc_id

    async def test_unknown_id_404(self, client: AsyncClient, tenant_admin_token: str) -> None:
        resp = await client.get(
            "/api/v1/use-cases/00000000-0000-0000-0000-000000999999",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 404


class TestUpdate:
    async def test_patch_status_and_owner(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        uc_id = created.json()["id"]

        owner_id = "11111111-1111-1111-1111-111111111111"
        resp = await client.patch(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(tenant_admin_token),
            json={"status": "ACTIVE", "owner_user_id": owner_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ACTIVE"
        assert body["owner_user_id"] == owner_id

    async def test_patch_can_clear_owner(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(owner_user_id="11111111-1111-1111-1111-111111111111"),
        )
        uc_id = created.json()["id"]

        resp = await client.patch(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(tenant_admin_token),
            json={"owner_user_id": None},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["owner_user_id"] is None

    async def test_patch_unspecified_fields_untouched(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(notes="initial notes"),
        )
        uc_id = created.json()["id"]

        # Patch only the title; notes should stay.
        resp = await client.patch(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(tenant_admin_token),
            json={"title": "Revised title"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Revised title"
        assert body["notes"] == "initial notes"

    async def test_viewer_cannot_patch(
        self,
        client: AsyncClient,
        tenant_admin_token: str,
        viewer_token: str,
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        uc_id = created.json()["id"]

        resp = await client.patch(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(viewer_token),
            json={"title": "x"},
        )
        assert resp.status_code == 403


class TestDelete:
    async def test_delete_soft_retires(self, client: AsyncClient, tenant_admin_token: str) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(status="ACTIVE"),
        )
        uc_id = created.json()["id"]

        resp = await client.delete(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(tenant_admin_token),
        )
        assert resp.status_code == 204

        # Still fetchable, status now RETIRED.
        after = await client.get(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(tenant_admin_token),
        )
        assert after.status_code == 200
        assert after.json()["status"] == "RETIRED"

    async def test_viewer_cannot_delete(
        self,
        client: AsyncClient,
        tenant_admin_token: str,
        viewer_token: str,
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        uc_id = created.json()["id"]

        resp = await client.delete(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403


class TestTenantIsolation:
    async def test_other_tenant_cannot_see(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        assert created.status_code == 201

        other_token = _second_tenant_admin_token()

        # Other tenant's listing must not include it.
        list_resp = await client.get("/api/v1/use-cases", headers=auth_header(other_token))
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 0

        # And direct GET must 404 (not 403 — we don't leak existence).
        uc_id = created.json()["id"]
        detail_resp = await client.get(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(other_token),
        )
        assert detail_resp.status_code == 404

    async def test_other_tenant_cannot_patch(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        uc_id = created.json()["id"]

        other_token = _second_tenant_admin_token()
        resp = await client.patch(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(other_token),
            json={"title": "stealing this"},
        )
        assert resp.status_code == 404

    async def test_other_tenant_cannot_delete(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        created = await client.post(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            json=_valid_payload(),
        )
        uc_id = created.json()["id"]

        other_token = _second_tenant_admin_token()
        resp = await client.delete(
            f"/api/v1/use-cases/{uc_id}",
            headers=auth_header(other_token),
        )
        assert resp.status_code == 404


class TestCountsIsolation:
    """Counts endpoint must respect tenant isolation too."""

    async def test_counts_only_own_tenant(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        # Tenant A creates 3.
        for _ in range(3):
            await client.post(
                "/api/v1/use-cases",
                headers=auth_header(tenant_admin_token),
                json=_valid_payload(),
            )

        # Tenant B counts must be zero.
        other_token = _second_tenant_admin_token()
        resp = await client.get(
            "/api/v1/use-cases/counts",
            headers=auth_header(other_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["draft"] == 0


@pytest_asyncio.fixture(autouse=True)
async def _seed_principals(seeded_principals) -> None:
    """These endpoints persist the caller's user id into an FK column."""


# Ids these tests hand to the API as owner / survey-response references. Both
# columns are FKs, so on Postgres the rows have to exist; SQLite let the tests
# pass with dangling references.
OWNER_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOT_RESPONSE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest_asyncio.fixture(autouse=True)
async def _seed_fk_targets(_seed_principals) -> None:
    """Create the owner user and the survey-response chain the tests reference."""
    from app.models.survey import SurveyDelivery, SurveyResponse, SurveyTemplate
    from app.models.user import User
    from tests.conftest import TEST_TENANT_ID, TestSessionLocal, ensure_tenant

    async with TestSessionLocal() as session:
        await ensure_tenant(session, TEST_TENANT_ID)

        if await session.get(User, OWNER_USER_ID) is None:
            session.add(
                User(
                    id=OWNER_USER_ID,
                    tenant_id=TEST_TENANT_ID,
                    email="owner@test.local",
                    full_name="Use Case Owner",
                    hashed_password="placeholder",
                    is_active=True,
                    is_email_verified=True,
                    is_test_data=True,
                )
            )

        if await session.get(SurveyResponse, BOT_RESPONSE_ID) is None:
            template_id = uuid.uuid4()
            delivery_id = uuid.uuid4()
            session.add(
                SurveyTemplate(
                    id=template_id,
                    tenant_id=TEST_TENANT_ID,
                    slug="use-case-test",
                    name="Use Case Test Template",
                    questions_json="[]",
                    is_test_data=True,
                )
            )
            await session.flush()
            session.add(
                SurveyDelivery(
                    id=delivery_id,
                    tenant_id=TEST_TENANT_ID,
                    template_id=template_id,
                    name="Use Case Test Delivery",
                    audience_filter_json="{}",
                    audience_label="All",
                    is_test_data=True,
                )
            )
            await session.flush()
            session.add(
                SurveyResponse(
                    id=BOT_RESPONSE_ID,
                    tenant_id=TEST_TENANT_ID,
                    delivery_id=delivery_id,
                    is_test_data=True,
                )
            )
        await session.commit()
