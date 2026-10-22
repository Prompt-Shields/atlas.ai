"""E2E: the calls the console's Work client page makes (promptly-guide#52).

The page (frontend/src/app/dashboard/work-client) reads the 90-day status from
GET /billing/entitlements and sends PATCH /billing/work-client. This checks those
fields and changes on the running stack, as a fresh tenant admin sees them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from tests.e2e.stack import BASE_URL, TENANT_PASS, auth

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

# What frontend/src/lib/work-client.ts reads (WorkClientEntitlements).
PAGE_FIELDS = {
    "plan",
    "modules",
    "free_client_status",
    "work_client_started_at",
    "work_client_days",
    "free_client_grace_ends_at",
    "paid_module_active",
    "own_endpoint_declared",
}


async def _signup(client: httpx.AsyncClient, label: str) -> str:
    resp = await client.post(
        f"{BASE_URL}/signup",
        json={
            "email": f"{label}-owner@example.com",
            "password": TENANT_PASS,
            "tenant_name": f"E2E {label}",
            "tenant_slug": f"e2e-{label}",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def test_work_client_page_flow() -> None:
    label = f"wc-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _signup(client, label)

        resp = await client.get(f"{BASE_URL}/billing/entitlements", headers=auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert PAGE_FIELDS <= body.keys()
        # A new tenant has not started the Work client: the page shows "Not started".
        assert body["free_client_status"] is None
        assert body["work_client_started_at"] is None
        assert body["own_endpoint_declared"] is False

        # "Record today as day 0".
        resp = await client.patch(
            f"{BASE_URL}/billing/work-client", headers=auth(token), json={"started": True}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["free_client_status"] == "active"
        assert body["work_client_days"] == 0
        started = datetime.fromisoformat(body["work_client_started_at"])
        ends = datetime.fromisoformat(body["free_client_grace_ends_at"])
        # The page takes the length of the rule from these two dates.
        assert ends - started == timedelta(days=90)

        # Declare, then withdraw, the organisation's own endpoint.
        resp = await client.patch(
            f"{BASE_URL}/billing/work-client",
            headers=auth(token),
            json={"own_endpoint_declared": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["own_endpoint_declared"] is True
        assert resp.json()["free_client_status"] == "active"

        resp = await client.patch(
            f"{BASE_URL}/billing/work-client",
            headers=auth(token),
            json={"own_endpoint_declared": False},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["own_endpoint_declared"] is False
        # Day 0 stays where it was.
        assert datetime.fromisoformat(resp.json()["work_client_started_at"]) == started
