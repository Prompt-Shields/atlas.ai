"""Endpoint tests for POST /api/v1/telemetry/prompt-events."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.auth.api_keys import generate_api_key
from app.models.prompt_event import PromptEvent
from app.models.user import APIKey, User

# Module-level import — avoids repeating the inline import in every test body.
from tests.conftest import TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit]

VALID_HASH = hashlib.sha256(b"hello").hexdigest()

# Use proper-looking UUIDs (not all-zeros with a small int) so that
# SQLite's sqlalchemy.dialects.postgresql.UUID stores them as hex strings,
# not as integers. All-zero UUIDs like 00000000-...-0002 get stored as
# the integer 2 by SQLite's affinity rules, causing a result-processor crash.
TEST_USER_ID = uuid.UUID("a1b2c3d4-0001-4000-8000-000000000002")

# TELEMETRY_TENANT_ID is intentionally distinct from conftest's TEST_TENANT_ID:
#   (a) conftest's TEST_TENANT_ID uses all-zeros UUIDs that hit the SQLite
#       type-affinity crash described above for UUID columns.
#   (b) Aggregate tests (Task 6) authenticate via JWT; the JWT tenant must
#       match the tenant of the seeded rows, so they should seed with this
#       same constant and mint a token accordingly.
TELEMETRY_TENANT_ID = uuid.UUID("a1b2c3d4-0001-4000-8000-000000000010")


def violation(**overrides):
    base = {
        "source": "safari_extension",
        "event_kind": "violation",
        "app_id": "chatgpt",
        "prompt_hash": VALID_HASH,
        "action": "redacted",
        "severity": "high",
        "pii_categories": {"email": 2},
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def tenant_api_key(setup_database):
    """Create a User + APIKey row for TELEMETRY_TENANT_ID and return the full key."""
    full_key, key_prefix, hashed_key = generate_api_key()
    # Pre-assign the API key ID so we can return it without a refresh after commit.
    api_key_id = uuid.uuid4()

    async with TestSessionLocal() as session:
        # APIKey.tenant_id is a FK to grc.tenants; Postgres enforces it even
        # though SQLite silently does not.
        await ensure_tenant(session, TELEMETRY_TENANT_ID, name="Telemetry Test Tenant")

        # Minimal User row — only required non-nullable columns
        user = User(
            id=TEST_USER_ID,
            email="apikey-test-user@test.local",
            hashed_password="placeholder",
            full_name="API Key Test User",
            is_active=True,
            is_email_verified=True,
            is_test_data=True,
        )
        session.add(user)
        await session.flush()

        api_key = APIKey(
            id=api_key_id,
            user_id=TEST_USER_ID,
            tenant_id=TELEMETRY_TENANT_ID,
            key_prefix=key_prefix,
            hashed_key=hashed_key,
            name="test-ingest-key",
            is_active=True,
            is_test_data=True,
        )
        session.add(api_key)
        await session.commit()

    return full_key, api_key_id


@pytest_asyncio.fixture
async def unscoped_api_key(setup_database):
    """Create an APIKey with tenant_id=None (super-admin style)."""
    user_id = uuid.UUID("a1b2c3d4-0001-4000-8000-000000000099")
    full_key, key_prefix, hashed_key = generate_api_key()

    async with TestSessionLocal() as session:
        user = User(
            id=user_id,
            email="unscoped-user@test.local",
            hashed_password="placeholder",
            full_name="Unscoped User",
            is_active=True,
            is_email_verified=True,
            is_test_data=True,
        )
        session.add(user)
        await session.flush()

        api_key = APIKey(
            user_id=user_id,
            tenant_id=None,
            key_prefix=key_prefix,
            hashed_key=hashed_key,
            name="unscoped-key",
            is_active=True,
            is_test_data=True,
        )
        session.add(api_key)
        await session.commit()

    return full_key


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_requires_api_key(client):
    """POST without X-API-Key header → 401."""
    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [violation()]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingests_valid_batch(client, tenant_api_key):
    """2 valid events → 200, {ingested: 2, skipped: 0}, 2 DB rows."""
    full_key, api_key_id = tenant_api_key

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [violation(), violation()]},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingested"] == 2
    assert body["skipped"] == 0

    async with TestSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(PromptEvent).where(PromptEvent.tenant_id == TELEMETRY_TENANT_ID)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    for row in rows:
        assert row.tenant_id == TELEMETRY_TENANT_ID
        assert row.api_key_id == api_key_id


@pytest.mark.asyncio
async def test_bad_row_skipped_not_fatal(client, tenant_api_key):
    """One valid + one bad prompt_hash → ingested 1, skipped 1, reason mentions prompt_hash."""
    full_key, _ = tenant_api_key

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [violation(), violation(prompt_hash="not-a-hash")]},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingested"] == 1
    assert body["skipped"] == 1
    assert any("prompt_hash" in r for r in body["skipped_reasons"])


@pytest.mark.asyncio
async def test_non_dict_row_skipped_not_fatal(client, tenant_api_key):
    """Non-dict rows (string, None) skip per-row rather than 422-ing the whole batch."""
    full_key, _ = tenant_api_key

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [violation(), "garbage", None]},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingested"] == 1
    assert body["skipped"] == 2


@pytest.mark.asyncio
async def test_prompt_text_row_skipped_with_reason(client, tenant_api_key):
    """Extra field prompt_text → ingested 0, skipped 1 (privacy contract)."""
    full_key, _ = tenant_api_key

    event = violation()
    event["prompt_text"] = "the actual secret prompt"

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [event]},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingested"] == 0
    assert body["skipped"] == 1
    assert len(body["skipped_reasons"]) == 1


@pytest.mark.asyncio
async def test_empty_batch_rejected(client, tenant_api_key):
    """Empty events list → 422."""
    full_key, _ = tenant_api_key

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": []},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_batch_over_500_rejected(client, tenant_api_key):
    """501 events → 422."""
    full_key, _ = tenant_api_key

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [violation() for _ in range(501)]},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_occurred_at_defaults_to_now(client, tenant_api_key):
    """Event without occurred_at → row has occurred_at >= just-before timestamp."""
    full_key, _ = tenant_api_key

    event = violation()
    # No occurred_at field
    before = datetime.now(UTC)

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [event]},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(PromptEvent).where(PromptEvent.tenant_id == TELEMETRY_TENANT_ID)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    stored = rows[0].occurred_at
    # SQLite may return naive datetimes — normalize
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored >= before


@pytest.mark.asyncio
async def test_naive_occurred_at_treated_as_utc(client, tenant_api_key):
    """Naive ISO timestamp (no timezone) → stored as UTC, equals the sent value."""
    full_key, _ = tenant_api_key

    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [violation(occurred_at="2026-06-10T12:00:00")]},
        headers={"X-API-Key": full_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingested"] == 1
    assert body["skipped"] == 0

    async with TestSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(PromptEvent).where(PromptEvent.tenant_id == TELEMETRY_TENANT_ID)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    stored = rows[0].occurred_at
    # SQLite may return naive datetimes — normalize before comparing
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored == datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_unscoped_api_key_refused(client, unscoped_api_key):
    """APIKey with tenant_id=None → 401."""
    resp = await client.post(
        "/api/v1/telemetry/prompt-events",
        json={"events": [violation()]},
        headers={"X-API-Key": unscoped_api_key},
    )
    assert resp.status_code == 401
