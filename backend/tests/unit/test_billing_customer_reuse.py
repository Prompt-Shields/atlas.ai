"""Unit tests: a tenant keeps one Stripe customer across checkouts.

- ``checkout_customer_params``: no customer yet → ``customer_email``; an
  existing customer → ``customer`` plus ``customer_update`` (automatic tax).
- ``checkout.session.completed``: stores the customer when the tenant has
  none; never overwrites an existing one, and warns when a different one
  arrives.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.routers.billing import checkout_customer_params
from app.services.billing import webhooks as wh
from tests.conftest import TestSessionLocal, ensure_tenant

# ---------------------------------------------------------------------------
# checkout_customer_params
# ---------------------------------------------------------------------------


def test_no_customer_yet_sends_email_only():
    params = checkout_customer_params(
        stripe_customer_id=None, email="a@acme.test", automatic_tax_enabled=True
    )
    assert params == {"customer_email": "a@acme.test"}


def test_empty_customer_id_counts_as_none():
    params = checkout_customer_params(
        stripe_customer_id="", email="a@acme.test", automatic_tax_enabled=True
    )
    assert params == {"customer_email": "a@acme.test"}


def test_existing_customer_is_reused_with_customer_update():
    params = checkout_customer_params(
        stripe_customer_id="cus_existing", email="a@acme.test", automatic_tax_enabled=True
    )
    # Stripe rejects customer together with customer_email.
    assert params == {
        "customer": "cus_existing",
        "customer_update": {"address": "auto", "name": "auto"},
    }


def test_existing_customer_without_automatic_tax_has_no_customer_update():
    params = checkout_customer_params(
        stripe_customer_id="cus_existing", email="a@acme.test", automatic_tax_enabled=False
    )
    assert params == {"customer": "cus_existing"}


# ---------------------------------------------------------------------------
# checkout.session.completed handler
# ---------------------------------------------------------------------------


class _Log:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw) -> None:
        self.warnings.append((event, kw))

    def info(self, *_a, **_kw) -> None:
        pass


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
def fake_log(monkeypatch) -> _Log:
    log = _Log()
    monkeypatch.setattr(wh, "log", log)
    monkeypatch.setattr(wh, "_fetch_subscription_item_id", lambda sub_id: "si_1")
    return log


async def _tenant(db: AsyncSession, customer_id: str | None) -> uuid.UUID:
    tid = uuid.uuid4()
    t = await ensure_tenant(db, tid)
    t.stripe_customer_id = customer_id
    await db.commit()
    return tid


def _completed(tid: uuid.UUID, customer: str | None) -> dict:
    return {
        "client_reference_id": str(tid),
        "metadata": {"tenant_id": str(tid)},
        "customer": customer,
        "subscription": "sub_1",
    }


@pytest.mark.asyncio
async def test_completed_stores_customer_when_tenant_has_none(db, fake_log):
    tid = await _tenant(db, None)
    await wh._on_checkout_completed(db, _completed(tid, "cus_new"))
    t = await db.get(Tenant, tid)
    assert t.stripe_customer_id == "cus_new"
    assert t.stripe_subscription_id == "sub_1"
    assert fake_log.warnings == []


@pytest.mark.asyncio
async def test_completed_keeps_same_existing_customer_silently(db, fake_log):
    tid = await _tenant(db, "cus_existing")
    await wh._on_checkout_completed(db, _completed(tid, "cus_existing"))
    t = await db.get(Tenant, tid)
    assert t.stripe_customer_id == "cus_existing"
    assert fake_log.warnings == []


@pytest.mark.asyncio
async def test_completed_never_overwrites_existing_customer(db, fake_log):
    tid = await _tenant(db, "cus_existing")
    await wh._on_checkout_completed(db, _completed(tid, "cus_other"))
    t = await db.get(Tenant, tid)
    assert t.stripe_customer_id == "cus_existing"
    assert [e for e, _ in fake_log.warnings] == ["webhook_checkout_customer_mismatch"]
    _, kw = fake_log.warnings[0]
    assert kw["stored_customer"] == "cus_existing"
    assert kw["incoming_customer"] == "cus_other"


@pytest.mark.asyncio
async def test_completed_without_customer_keeps_existing_one(db, fake_log):
    tid = await _tenant(db, "cus_existing")
    await wh._on_checkout_completed(db, _completed(tid, None))
    t = await db.get(Tenant, tid)
    assert t.stripe_customer_id == "cus_existing"
    assert fake_log.warnings == []
