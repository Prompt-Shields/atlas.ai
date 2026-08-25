"""SP-1 verification (#196): SaaS vendors don't leak into the use-case inventory.

A SaaS vendor is a `use_cases` row carrying a 1:1 `saas_vendor_profiles` row
(epic #187) but belongs to its own surface (/saas-vendor-ai). The AI-use-case
inventory list and its status counts must anti-join the profile out, matching
the /ai-inventory/aggregate guarantee.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from scripts.seed_saas_vendors import SEED_VENDORS, seed_vendors
from tests.conftest import (
    TEST_TENANT_ID,
    TestSessionLocal,
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _seed_vendors() -> int:
    async with TestSessionLocal() as db:
        n = await seed_vendors(db, TEST_TENANT_ID)
        await db.commit()
    return n


async def _create_real_use_case(client: AsyncClient, token: str) -> None:
    resp = await client.post(
        "/api/v1/use-cases",
        headers=auth_header(token),
        json={
            "title": "Grant memo drafting",
            "tool": "Microsoft Copilot",
            "department": "Grants Management",
            "source": "FORM",
            "data_classes": ["grantee_metadata"],
            "frequency": "weekly",
        },
    )
    assert resp.status_code == 201, resp.text


async def test_list_excludes_vendors(client: AsyncClient, tenant_admin_token: str) -> None:
    await _create_real_use_case(client, tenant_admin_token)
    seeded = await _seed_vendors()
    assert seeded == len(SEED_VENDORS)
    assert seeded > 0  # guard: seed must add vendor rows for this to be meaningful

    resp = await client.get("/api/v1/use-cases", headers=auth_header(tenant_admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert [uc["title"] for uc in body["use_cases"]] == ["Grant memo drafting"]


async def test_counts_excludes_vendors(client: AsyncClient, tenant_admin_token: str) -> None:
    await _create_real_use_case(client, tenant_admin_token)
    seeded = await _seed_vendors()
    assert seeded > 0

    resp = await client.get("/api/v1/use-cases/counts", headers=auth_header(tenant_admin_token))
    assert resp.status_code == 200, resp.text
    counts = resp.json()
    # only the one real (DRAFT) use-case is counted; vendors are anti-joined out
    assert counts["total"] == 1
    assert counts["draft"] == 1
