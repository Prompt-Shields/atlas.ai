"""The administrator's view of the guided-step pool, and the hard cap (promptly-guide #62).

`GET /billing/guided-steps` shows the pool and closed months' totals as step
counts only, and never the open month's count (promptly-guide decision P9);
`PUT /billing/guided-steps/hard-cap` turns the hard cap on or off. Both are for
tenant admins, scoped to the admin's own tenant, and a change is audited.

Pool sizes are worked out with `enterprise_pool()`, never written here.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.auth.jwt import create_access_token
from app.models.audit_log import AuditLog
from app.models.guided_step import GuidedStepCount
from app.models.tenant import Tenant
from app.models.user import Role, User, UserRole
from app.services.entitlements import WORK_CLIENT_LOOKUP_KEY
from app.services.guided_step_meter import (
    CLOSED_MONTHS_SHOWN,
    Refusal,
    check,
    period_of,
    pool_status,
    record,
)
from app.services.guided_steps import enterprise_pool
from tests.conftest import TestSessionLocal, auth_header

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

STATUS = "/api/v1/billing/guided-steps"
HARD_CAP = "/api/v1/billing/guided-steps/hard-cap"
SEATS = 3
EVENT = "billing.guided_step_hard_cap.updated"
FIELDS = {"period", "pool", "hard_cap", "own_endpoint", "work_client", "closed_months"}
# A count no pool, period or other figure in the reply could equal by chance.
OPEN_MONTH_STEPS = 7919


def _subs(seats: int = SEATS) -> dict:
    return {
        "sub_wc": {
            "kind": "enterprise",
            "status": "active",
            "items": {WORK_CLIENT_LOOKUP_KEY: seats},
        }
    }


async def _seed(
    *, role: Role = Role.TENANT_ADMIN, work_client: bool = True, steps: int = 0
) -> tuple[uuid.UUID, str]:
    """A tenant and one user with `role`; `steps` hosted steps served this month."""
    tid, uid = uuid.uuid4(), uuid.uuid4()
    email = f"admin-{uid.hex[:8]}@acme.test"
    async with TestSessionLocal() as db:
        tenant = Tenant(id=tid, name="Acme", slug=f"acme-{tid.hex}", is_active=True)
        tenant.stripe_subscriptions = _subs() if work_client else {}
        db.add(tenant)
        db.add(
            User(
                id=uid,
                email=email,
                hashed_password="x",
                full_name="Admin",
                tenant_id=tid,
                is_email_verified=True,
                is_active=True,
            )
        )
        await db.flush()
        db.add(UserRole(user_id=uid, role=role, tenant_id=tid))
        if steps:
            await record(db, tenant, report_id="seed", served_at=await _db_now(db), steps=steps)
        await db.commit()
    token = create_access_token(
        user_id=uid, email=email, roles=[role.value], tenant_id=tid, org_id=None
    )
    return tid, token


async def _counts(tid: uuid.UUID, counts: dict[str, int]) -> None:
    """Hosted steps already counted for `tid`, by month ("YYYY-MM")."""
    async with TestSessionLocal() as db:
        for period, steps in counts.items():
            db.add(GuidedStepCount(id=uuid.uuid4(), tenant_id=tid, period=period, steps=steps))
        await db.commit()


async def _open_period() -> str:
    async with TestSessionLocal() as db:
        return period_of(await _db_now(db))


def _months_before(period: str, n: int) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + (month - 1) - n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


async def _db_now(db):
    now = await db.scalar(select(func.now()))
    return now if now.tzinfo else now.replace(tzinfo=UTC)


async def _hard_cap(tid: uuid.UUID) -> bool:
    async with TestSessionLocal() as db:
        return (await db.get(Tenant, tid)).guided_step_hard_cap


async def _audit(tid: uuid.UUID) -> list[AuditLog]:
    async with TestSessionLocal() as db:
        rows = await db.execute(
            select(AuditLog).where(AuditLog.event_type == EVENT, AuditLog.tenant_id == tid)
        )
        return list(rows.scalars())


# ── The month ──────────────────────────────────────────────────────────────


async def test_admin_sees_the_pool_and_not_the_open_months_count(client: AsyncClient) -> None:
    pool = enterprise_pool(SEATS)
    _, token = await _seed(steps=pool - 1)
    resp = await client.get(STATUS, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == FIELDS
    assert body["pool"] == pool
    assert body["hard_cap"] is False
    assert body["work_client"] is True
    assert body["own_endpoint"] is False
    assert body["period"] == await _open_period()
    assert body["closed_months"] == []


async def test_the_open_months_count_never_appears_in_the_reply(client: AsyncClient) -> None:
    tid, token = await _seed()
    period = await _open_period()
    await _counts(tid, {period: OPEN_MONTH_STEPS, _months_before(period, 1): 3})
    replies = [
        await client.get(STATUS, headers=auth_header(token)),
        await client.put(HARD_CAP, json={"hard_cap": True}, headers=auth_header(token)),
        await client.put(HARD_CAP, json={"hard_cap": False}, headers=auth_header(token)),
    ]
    for resp in replies:
        assert resp.status_code == 200, resp.text
        assert str(OPEN_MONTH_STEPS) not in resp.text
        body = resp.json()
        for key in ("used", "remaining", "overage"):
            assert key not in body
        assert [m["period"] for m in body["closed_months"]] == [_months_before(period, 1)]


async def test_the_open_month_is_still_counted_exactly_for_billing(client: AsyncClient) -> None:
    pool = enterprise_pool(SEATS)
    tid, token = await _seed(steps=pool + 4)
    assert "used" not in (await client.get(STATUS, headers=auth_header(token))).json()
    async with TestSessionLocal() as db:
        status = await pool_status(db, await db.get(Tenant, tid), now=await _db_now(db))
    assert status.used == pool + 4
    assert status.overage == 4


async def test_closed_months_show_their_totals_newest_first(client: AsyncClient) -> None:
    tid, token = await _seed()
    period = await _open_period()
    await _counts(
        tid,
        {
            period: OPEN_MONTH_STEPS,
            _months_before(period, 1): 41,
            _months_before(period, 3): 12,
        },
    )
    body = (await client.get(STATUS, headers=auth_header(token))).json()
    assert body["closed_months"] == [
        {"period": _months_before(period, 1), "used": 41},
        {"period": _months_before(period, 3), "used": 12},
    ]


async def test_only_the_latest_closed_months_are_listed(client: AsyncClient) -> None:
    tid, token = await _seed()
    period = await _open_period()
    extra = 2
    await _counts(
        tid, {_months_before(period, n): n for n in range(1, CLOSED_MONTHS_SHOWN + extra + 1)}
    )
    months = (await client.get(STATUS, headers=auth_header(token))).json()["closed_months"]
    assert [m["used"] for m in months] == list(range(1, CLOSED_MONTHS_SHOWN + 1))


async def test_the_reply_carries_counts_never_money(client: AsyncClient) -> None:
    tid, token = await _seed()
    await _counts(tid, {_months_before(await _open_period(), 1): 5})
    body = (await client.get(STATUS, headers=auth_header(token))).json()
    keys = set(body) | {key for month in body["closed_months"] for key in month}
    for key in keys:
        assert not any(word in key for word in ("price", "amount", "cost", "eur", "usd"))
    assert isinstance(body["pool"], int)
    assert all(isinstance(m["used"], int) for m in body["closed_months"])


async def test_no_work_client_has_no_pool(client: AsyncClient) -> None:
    _, token = await _seed(work_client=False)
    body = (await client.get(STATUS, headers=auth_header(token))).json()
    assert body["work_client"] is False
    assert body["pool"] == 0 and body["closed_months"] == []


async def test_admin_sees_only_their_own_tenant(client: AsyncClient) -> None:
    tid_a, token_a = await _seed()
    tid_b, _ = await _seed()
    previous = _months_before(await _open_period(), 1)
    await _counts(tid_a, {previous: 2})
    await _counts(tid_b, {previous: 7})
    months = (await client.get(STATUS, headers=auth_header(token_a))).json()["closed_months"]
    assert months == [{"period": previous, "used": 2}]


# ── The hard cap ───────────────────────────────────────────────────────────


async def test_admin_turns_the_hard_cap_on_and_off(client: AsyncClient) -> None:
    tid, token = await _seed()
    resp = await client.put(HARD_CAP, json={"hard_cap": True}, headers=auth_header(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["hard_cap"] is True
    assert await _hard_cap(tid) is True

    resp = await client.put(HARD_CAP, json={"hard_cap": False}, headers=auth_header(token))
    assert resp.json()["hard_cap"] is False
    assert await _hard_cap(tid) is False


async def test_a_change_is_audited_and_a_repeat_is_not(client: AsyncClient) -> None:
    tid, token = await _seed()
    await client.put(HARD_CAP, json={"hard_cap": False}, headers=auth_header(token))
    assert await _audit(tid) == []

    await client.put(HARD_CAP, json={"hard_cap": True}, headers=auth_header(token))
    await client.put(HARD_CAP, json={"hard_cap": True}, headers=auth_header(token))
    entries = await _audit(tid)
    assert len(entries) == 1
    assert json.loads(entries[0].details) == {"previous": False, "current": True}
    assert entries[0].actor_email.startswith("admin-")
    assert entries[0].resource_id == str(tid)


async def test_the_hard_cap_stops_hosted_steps_at_the_pool(client: AsyncClient) -> None:
    tid, token = await _seed(steps=enterprise_pool(SEATS))
    await client.put(HARD_CAP, json={"hard_cap": True}, headers=auth_header(token))
    async with TestSessionLocal() as db:
        tenant = await db.get(Tenant, tid)
        verdict = await check(db, tenant, now=await _db_now(db))
    assert not verdict.allowed
    assert verdict.refusal is Refusal.POOL_USED


@pytest.mark.parametrize(
    "body", [{"hard_cap": "yes"}, {"hard_cap": 1}, {}, {"hard_cap": True, "x": 1}]
)
async def test_a_malformed_change_is_refused(client: AsyncClient, body: dict) -> None:
    tid, token = await _seed()
    resp = await client.put(HARD_CAP, json=body, headers=auth_header(token))
    assert resp.status_code == 422
    assert await _hard_cap(tid) is False


async def test_the_change_touches_only_the_admins_own_tenant(client: AsyncClient) -> None:
    tid_a, token_a = await _seed()
    tid_b, _ = await _seed()
    await client.put(HARD_CAP, json={"hard_cap": True}, headers=auth_header(token_a))
    assert await _hard_cap(tid_a) is True
    assert await _hard_cap(tid_b) is False


# ── Who may ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.ORG_ADMIN, Role.ANALYST, Role.VIEWER])
async def test_only_tenant_admins_see_or_change_it(client: AsyncClient, role: Role) -> None:
    tid, token = await _seed(role=role)
    assert (await client.get(STATUS, headers=auth_header(token))).status_code == 403
    resp = await client.put(HARD_CAP, json={"hard_cap": True}, headers=auth_header(token))
    assert resp.status_code == 403
    assert await _hard_cap(tid) is False
    assert await _audit(tid) == []


async def test_no_token_no_reply(client: AsyncClient) -> None:
    assert (await client.get(STATUS)).status_code == 401
    assert (await client.put(HARD_CAP, json={"hard_cap": True})).status_code == 401
