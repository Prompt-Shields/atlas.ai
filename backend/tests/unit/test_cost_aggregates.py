"""Tests for the cost aggregate read endpoints (``app.routers.cost``).

Runs on the default SQLite unit suite. We seed a handful of ``AICostRecord``
rows for ONE tenant via ``TestSessionLocal`` and mint a matching JWT, then hit
the read endpoints through the ``client`` fixture.

SQLite UUID trap: all-zero UUIDs get stored as integers by SQLite type
affinity, so we use non-zero UUIDs for the tenant/user/integration ids.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

from app.auth.jwt import create_access_token
from app.models.ai_cost_record import (
    AICostRecord,
    CostKind,
    CostProvider,
    CostSource,
    CostSubjectKind,
)
from app.models.integration import Integration, IntegrationProvider
from tests.conftest import TestSessionLocal, auth_header, ensure_tenant

pytestmark = [pytest.mark.unit]

# Non-zero UUIDs — conftest's all-zeros TEST_TENANT_ID hits SQLite's integer
# type-affinity trap. The JWT tenant must match the seeded rows' tenant.
AGG_TENANT_ID = uuid.UUID("a9919999-0001-4000-8000-000000000010")
AGG_USER_ID = uuid.UUID("a9919999-0001-4000-8000-000000000002")
AGG_ORG_ID = uuid.UUID("a9919999-0001-4000-8000-000000000010")
INT_A = uuid.UUID("a9919999-0001-4000-8000-0000000000a1")  # anthropic
INT_B = uuid.UUID("a9919999-0001-4000-8000-0000000000b2")  # openai

DAY1 = date(2025, 8, 1)
DAY2 = date(2025, 8, 2)
DAY3 = date(2025, 8, 3)


@pytest.fixture
def tenant_admin_token() -> str:
    return create_access_token(
        user_id=AGG_USER_ID,
        email="cost-agg@test.local",
        roles=["TENANT_ADMIN"],
        tenant_id=AGG_TENANT_ID,
        org_id=AGG_ORG_ID,
    )


def _row(**kw: object) -> AICostRecord:
    defaults: dict[str, object] = {
        "tenant_id": AGG_TENANT_ID,
        "integration_id": INT_A,
        "provider": CostProvider.anthropic,
        "usage_date": DAY1,
        "cost_kind": CostKind.metered_usage,
        "subject_kind": CostSubjectKind.model,
        "subject_ref": "",
        "cost_source": CostSource.vendor_reported,
        "is_provisional": False,
    }
    defaults.update(kw)
    return AICostRecord(**defaults)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def seeded(setup_database) -> None:
    """Seed ~6 rows spanning 3 days, 2 providers, mixed source/subject."""
    async with TestSessionLocal() as session:
        # AICostRecord carries FKs to grc.tenants and grc.integrations.
        # Postgres enforces both; SQLite silently does not, which is why these
        # rows seeded fine locally and failed only in CI.
        await ensure_tenant(session, AGG_TENANT_ID, name="Cost Aggregate Test Tenant")
        session.add_all(
            [
                Integration(
                    id=INT_A,
                    tenant_id=AGG_TENANT_ID,
                    provider=IntegrationProvider.ANTHROPIC,
                    display_name="Anthropic (test)",
                    is_test_data=True,
                ),
                Integration(
                    id=INT_B,
                    tenant_id=AGG_TENANT_ID,
                    provider=IntegrationProvider.OPENAI,
                    display_name="OpenAI (test)",
                    is_test_data=True,
                ),
            ]
        )
        await session.flush()

        rows = [
            # DAY1 — anthropic, vendor_reported, model
            _row(
                usage_date=DAY1,
                integration_id=INT_A,
                provider=CostProvider.anthropic,
                subject_kind=CostSubjectKind.model,
                subject_ref="claude-opus-4",
                cost_source=CostSource.vendor_reported,
                cost_usd=Decimal("10.00"),
            ),
            # DAY1 — openai, vendor_reported, model
            _row(
                usage_date=DAY1,
                integration_id=INT_B,
                provider=CostProvider.openai,
                subject_kind=CostSubjectKind.model,
                subject_ref="gpt-4o",
                cost_source=CostSource.vendor_reported,
                cost_usd=Decimal("5.00"),
            ),
            # DAY2 — anthropic, derived_tokens, member
            _row(
                usage_date=DAY2,
                integration_id=INT_A,
                provider=CostProvider.anthropic,
                subject_kind=CostSubjectKind.member,
                subject_ref="alice@corp.com",
                cost_source=CostSource.derived_tokens,
                cost_usd=Decimal("3.00"),
            ),
            # DAY2 — openai, vendor_reported, model
            _row(
                usage_date=DAY2,
                integration_id=INT_B,
                provider=CostProvider.openai,
                subject_kind=CostSubjectKind.model,
                subject_ref="gpt-4o",
                cost_source=CostSource.vendor_reported,
                cost_usd=Decimal("7.00"),
            ),
            # DAY3 — anthropic, vendor_reported, model, PROVISIONAL
            _row(
                usage_date=DAY3,
                integration_id=INT_A,
                provider=CostProvider.anthropic,
                subject_kind=CostSubjectKind.model,
                subject_ref="claude-opus-4",
                cost_source=CostSource.vendor_reported,
                cost_usd=Decimal("2.00"),
                is_provisional=True,
            ),
            # DAY3 — anthropic, derived_seats, member
            _row(
                usage_date=DAY3,
                integration_id=INT_A,
                provider=CostProvider.anthropic,
                subject_kind=CostSubjectKind.member,
                subject_ref="bob@corp.com",
                cost_source=CostSource.derived_seats,
                cost_usd=Decimal("4.00"),
            ),
        ]
        session.add_all(rows)
        await session.commit()


def _range() -> dict[str, str]:
    return {"since": "2025-08-01", "until": "2025-08-03"}


# ─── summary ─────────────────────────────────────────────────────────


async def test_summary_totals_and_splits(client, tenant_admin_token, seeded) -> None:
    resp = await client.get(
        "/api/v1/cost/summary",
        params=_range(),
        headers=auth_header(tenant_admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # total = 10+5+3+7+2+4 = 31
    assert Decimal(str(body["total_cost_usd"])) == Decimal("31.00")
    # vendor_reported = 10+5+7+2 = 24
    assert Decimal(str(body["vendor_reported_usd"])) == Decimal("24.00")
    # derived = 3 (tokens) + 4 (seats) = 7
    assert Decimal(str(body["derived_usd"])) == Decimal("7.00")
    # provisional = 2
    assert Decimal(str(body["provisional_usd"])) == Decimal("2.00")
    # active connectors = 2 distinct integration_ids
    assert body["active_connectors"] == 2


async def test_summary_provider_filter(client, tenant_admin_token, seeded) -> None:
    resp = await client.get(
        "/api/v1/cost/summary",
        params={**_range(), "provider": "openai"},
        headers=auth_header(tenant_admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # only the two openai rows: 5 + 7 = 12
    assert Decimal(str(body["total_cost_usd"])) == Decimal("12.00")
    assert body["active_connectors"] == 1


# ─── timeseries ──────────────────────────────────────────────────────


async def test_timeseries_buckets(client, tenant_admin_token, seeded) -> None:
    resp = await client.get(
        "/api/v1/cost/timeseries",
        params=_range(),
        headers=auth_header(tenant_admin_token),
    )
    assert resp.status_code == 200, resp.text
    points = resp.json()
    assert [p["date"] for p in points] == ["2025-08-01", "2025-08-02", "2025-08-03"]
    by_date = {p["date"]: p for p in points}
    assert Decimal(str(by_date["2025-08-01"]["cost_usd"])) == Decimal("15.00")
    assert Decimal(str(by_date["2025-08-02"]["cost_usd"])) == Decimal("10.00")
    assert Decimal(str(by_date["2025-08-03"]["cost_usd"])) == Decimal("6.00")
    assert by_date["2025-08-01"]["is_provisional"] is False
    assert by_date["2025-08-03"]["is_provisional"] is True


# ─── breakdown ───────────────────────────────────────────────────────


async def test_breakdown_by_provider(client, tenant_admin_token, seeded) -> None:
    resp = await client.get(
        "/api/v1/cost/breakdown",
        params={**_range(), "by": "provider"},
        headers=auth_header(tenant_admin_token),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    # anthropic = 10+3+2+4 = 19, openai = 5+7 = 12 → anthropic first
    assert [r["key"] for r in rows] == ["anthropic", "openai"]
    assert Decimal(str(rows[0]["cost_usd"])) == Decimal("19.00")
    assert Decimal(str(rows[1]["cost_usd"])) == Decimal("12.00")
    # anthropic mixes vendor_reported + derived → "mixed"; openai all vendor.
    assert rows[0]["cost_source"] == "mixed"
    assert rows[1]["cost_source"] == "vendor_reported"


async def test_breakdown_by_model(client, tenant_admin_token, seeded) -> None:
    resp = await client.get(
        "/api/v1/cost/breakdown",
        params={**_range(), "by": "model"},
        headers=auth_header(tenant_admin_token),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    by_key = {r["key"]: Decimal(str(r["cost_usd"])) for r in rows}
    # gpt-4o = 5+7 = 12, claude-opus-4 = 10+2 = 12; only model subject rows.
    assert by_key == {"gpt-4o": Decimal("12.00"), "claude-opus-4": Decimal("12.00")}
    # members must be excluded.
    assert "alice@corp.com" not in by_key


async def test_breakdown_by_member(client, tenant_admin_token, seeded) -> None:
    resp = await client.get(
        "/api/v1/cost/breakdown",
        params={**_range(), "by": "member"},
        headers=auth_header(tenant_admin_token),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    # bob = 4 (seats), alice = 3 (tokens) → bob first.
    assert [r["key"] for r in rows] == ["bob@corp.com", "alice@corp.com"]
    assert Decimal(str(rows[0]["cost_usd"])) == Decimal("4.00")
    assert rows[0]["cost_source"] == "derived_seats"
    assert rows[1]["cost_source"] == "derived_tokens"


async def test_breakdown_invalid_by_400(client, tenant_admin_token, seeded) -> None:
    resp = await client.get(
        "/api/v1/cost/breakdown",
        params={**_range(), "by": "garbage"},
        headers=auth_header(tenant_admin_token),
    )
    assert resp.status_code == 400, resp.text
