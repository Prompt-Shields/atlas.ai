"""`GET /api/v1/guide/work-client` (promptly-guide #52): the free Work client's 90 days,
as a Guide Mac reads them with its Firebase token.

What these defend: Guide and Atlas's billing never disagree on the status; the answer
holds organisation-wide facts only, the status and the days left (E4), and not why an
organisation is exempt; and it needs Guide's token, not an Atlas login.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.auth.firebase_token import get_firebase_verifier
from app.auth.jwt import create_access_token
from app.main import app
from app.models.tenant import Plan, SubscriptionStatus
from tests.conftest import TEST_TENANT_ID, TestSessionLocal, auth_header, ensure_tenant
from tests.unit import firebase_keys as keys
from tests.unit.guide_db import guide_resolvers  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PATH = "/api/v1/guide/work-client"


@pytest.fixture(autouse=True)
def _local_google_keys():
    local = keys.verifier()
    app.dependency_overrides[get_firebase_verifier] = lambda: local
    yield
    app.dependency_overrides.pop(get_firebase_verifier, None)


def _admin() -> dict[str, str]:
    return auth_header(
        create_access_token(
            user_id=TEST_TENANT_ID,
            email="admin@example.com",
            roles=["TENANT_ADMIN"],
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_TENANT_ID,
        )
    )


async def _connect(client: AsyncClient) -> None:
    r = await client.put(
        "/api/v1/guide/connection",
        json={"firebase_project_id": keys.PROJECT, "offered_kinds": ["app"]},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text


async def _started(days_ago: float, **fields: object) -> None:
    async with TestSessionLocal() as s:
        now = await s.scalar(select(func.now()))
        tenant = await ensure_tenant(s, TEST_TENANT_ID)
        tenant.work_client_started_at = now - timedelta(days=days_ago)
        for name, value in fields.items():
            setattr(tenant, name, value)
        await s.commit()


async def _read(client: AsyncClient) -> dict:
    r = await client.get(PATH, headers=auth_header(keys.token()))
    assert r.status_code == 200, r.text
    return r.json()


async def test_only_the_status_and_the_days_left(client: AsyncClient) -> None:
    """Nothing about the person asking, and not why the organisation is exempt."""
    await _connect(client)
    await _started(40)
    assert await _read(client) == {"free_client_status": "active", "days_left": 50}


async def test_before_day_0_there_is_nothing_to_count_down(client: AsyncClient) -> None:
    await _connect(client)
    assert await _read(client) == {"free_client_status": None, "days_left": None}


async def test_days_left_round_up(client: AsyncClient) -> None:
    await _connect(client)
    await _started(89.5)
    assert await _read(client) == {"free_client_status": "grace_ending", "days_left": 1}


async def test_a_paid_module_keeps_the_hosted_model(client: AsyncClient) -> None:
    await _connect(client)
    await _started(120, plan=Plan.TEAM, subscription_status=SubscriptionStatus.ACTIVE)
    assert await _read(client) == {"free_client_status": "active", "days_left": 0}


async def test_guide_and_billing_say_the_same(client: AsyncClient) -> None:
    await _connect(client)
    await _started(95)
    guide = await _read(client)
    r = await client.get("/api/v1/billing/entitlements", headers=_admin())
    assert r.status_code == 200, r.text
    assert guide["free_client_status"] == r.json()["free_client_status"] == "endpoint_required"


async def test_it_needs_a_guide_token(client: AsyncClient) -> None:
    await _connect(client)
    assert (await client.get(PATH)).status_code == 401
    assert (await client.get(PATH, headers=_admin())).status_code == 401


async def test_an_unconnected_project_is_not_let_in(client: AsyncClient) -> None:
    r = await client.get(PATH, headers=auth_header(keys.token()))
    assert r.status_code == 401
