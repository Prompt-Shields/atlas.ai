"""Tests for GET /api/v1/telemetry/prompt-activity/* aggregate endpoints.

Auth pattern: JWT only (no API key).  Uses TELEMETRY_TENANT_ID from the
ingest test module — non-zero UUID that survives SQLite type affinity.

SQLite UUID trap recap: all-zeros UUIDs (like conftest's TEST_TENANT_ID)
get stored as integers by SQLite type affinity and crash UUID result
processing on retrieval.  We use TELEMETRY_TENANT_ID (non-zero) and seed
rows manually via TestSessionLocal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.auth.jwt import create_access_token
from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import PromptEventAction, PromptEventKind, PromptEventSource
from tests.conftest import TestSessionLocal

# Reuse constants defined in the ingest tests (same tenant, same UUID safety rationale).
from tests.unit.test_telemetry_ingest import TELEMETRY_TENANT_ID

pytestmark = [pytest.mark.unit]

# ── IDs for this module's fixtures ─────────────────────────────────────────────

# Non-zero user + org IDs (avoid SQLite integer-affinity trap)
AGG_USER_ID = uuid.UUID("a1b2c3d4-0002-4000-8000-000000000020")
AGG_ORG_ID = uuid.UUID("a1b2c3d4-0002-4000-8000-000000000030")

# A second tenant to verify isolation — also non-zero
OTHER_TENANT_ID = uuid.UUID("b2c3d4e5-0003-4000-8000-000000000050")

# Dates used in seeded rows
DAY_TODAY = datetime(2026, 6, 11, 10, 0, 0, tzinfo=UTC)
DAY_YESTERDAY = datetime(2026, 6, 10, 9, 0, 0, tzinfo=UTC)


# ── JWT / client helpers ────────────────────────────────────────────────────────


def _agg_token() -> str:
    """Mint a JWT for a TENANT_ADMIN with TELEMETRY_TENANT_ID."""
    return create_access_token(
        user_id=AGG_USER_ID,
        email="agg-test-user@test.local",
        roles=["TENANT_ADMIN"],
        tenant_id=TELEMETRY_TENANT_ID,
        org_id=AGG_ORG_ID,
    )


def _agg_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_agg_token()}"}


# ── Seed fixture ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_events(setup_database):
    """Seed three rows for TELEMETRY_TENANT_ID and one for OTHER_TENANT_ID.

    Row layout (the 'seed' described in the task spec):
      1. activity, safari_extension, chatgpt, occ=10, device="dev-1", today
      2. violation, safari_extension, chatgpt, redacted, pii={email:2, ssn:1},
         occ=1, device="dev-2", yesterday
      3. activity, sdk, app_id=None, occ=5, today
      4. (other tenant) activity, sdk, occ=99 — must never appear in results
    """
    async with TestSessionLocal() as session:
        # Row 1 — activity, safari_extension, chatgpt
        session.add(
            PromptEvent(
                tenant_id=TELEMETRY_TENANT_ID,
                source=PromptEventSource.SAFARI_EXTENSION,
                event_kind=PromptEventKind.ACTIVITY,
                app_id="chatgpt",
                occurrences=10,
                device_fingerprint="dev-1",
                occurred_at=DAY_TODAY,
                pii_categories={},
            )
        )
        # Row 2 — violation, safari_extension, chatgpt, pii, yesterday
        session.add(
            PromptEvent(
                tenant_id=TELEMETRY_TENANT_ID,
                source=PromptEventSource.SAFARI_EXTENSION,
                event_kind=PromptEventKind.VIOLATION,
                app_id="chatgpt",
                action=PromptEventAction.REDACTED,
                pii_categories={"email": 2, "ssn": 1},
                occurrences=1,
                device_fingerprint="dev-2",
                occurred_at=DAY_YESTERDAY,
            )
        )
        # Row 3 — activity, sdk, no app_id
        session.add(
            PromptEvent(
                tenant_id=TELEMETRY_TENANT_ID,
                source=PromptEventSource.SDK,
                event_kind=PromptEventKind.ACTIVITY,
                app_id=None,
                occurrences=5,
                device_fingerprint=None,
                occurred_at=DAY_TODAY,
                pii_categories={},
            )
        )
        # Row 4 — OTHER tenant (isolation check)
        session.add(
            PromptEvent(
                tenant_id=OTHER_TENANT_ID,
                source=PromptEventSource.SDK,
                event_kind=PromptEventKind.ACTIVITY,
                app_id="other-app",
                occurrences=99,
                device_fingerprint="dev-other",
                occurred_at=DAY_TODAY,
                pii_categories={},
            )
        )
        await session.commit()


# ── 1. summary endpoint ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_math(client, seeded_events):
    """summary covering both days: 16 prompts, 1 violation, 2 devices, 2 sources."""
    since = "2026-06-09T00:00:00Z"
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/summary",
        params={"since": since},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_prompts"] == 16
    assert body["total_violations"] == 1
    assert abs(body["violation_rate"] - 1 / 16) < 1e-9
    assert body["active_devices"] == 2
    assert body["active_sources"] == 2


@pytest.mark.asyncio
async def test_summary_empty_range(client, seeded_events):
    """No matching rows (since=2030) → all zeroes, violation_rate 0.0."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/summary",
        params={"since": "2030-01-01T00:00:00Z"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_prompts"] == 0
    assert body["total_violations"] == 0
    assert body["violation_rate"] == 0.0
    assert body["active_devices"] == 0
    assert body["active_sources"] == 0


@pytest.mark.asyncio
async def test_summary_source_filter(client, seeded_events):
    """source=sdk → 5 prompts, 0 violations."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/summary",
        params={"since": "2026-06-09T00:00:00Z", "source": "sdk"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_prompts"] == 5
    assert body["total_violations"] == 0
    assert body["violation_rate"] == 0.0


@pytest.mark.asyncio
async def test_summary_tenant_isolation(client, seeded_events):
    """Other tenant's 99-occurrence row must never appear in this tenant's summary."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/summary",
        params={"since": "2026-06-09T00:00:00Z"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # If other tenant leaks in, total_prompts would be 115 (16 + 99)
    assert body["total_prompts"] == 16


# ── 2. timeseries endpoint ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeseries_buckets(client, seeded_events):
    """Two buckets in ascending date order with correct sums."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/timeseries",
        params={"since": "2026-06-09T00:00:00Z"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    buckets = body["buckets"]
    assert len(buckets) == 2

    # Ascending order
    assert buckets[0]["date"] < buckets[1]["date"]

    # Yesterday: 1 violation row (occ=1)
    yesterday_bucket = next(b for b in buckets if b["date"] == "2026-06-10")
    assert yesterday_bucket["prompts"] == 1
    assert yesterday_bucket["violations"] == 1

    # Today: activity safari (occ=10) + activity sdk (occ=5) = 15, 0 violations
    today_bucket = next(b for b in buckets if b["date"] == "2026-06-11")
    assert today_bucket["prompts"] == 15
    assert today_bucket["violations"] == 0


# ── 3. breakdown endpoint ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_breakdown_by_app(client, seeded_events):
    """by=app: chatgpt→11, (none)→5; sorted by prompts desc."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/breakdown",
        params={"by": "app", "since": "2026-06-09T00:00:00Z"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["rows"]) == 2
    rows = {r["key"]: r for r in body["rows"]}
    assert "chatgpt" in rows
    assert rows["chatgpt"]["prompts"] == 11  # 10 + 1
    assert rows["chatgpt"]["violations"] == 1
    assert "(none)" in rows
    assert rows["(none)"]["prompts"] == 5
    # Sorted descending
    assert body["rows"][0]["prompts"] >= body["rows"][1]["prompts"]


@pytest.mark.asyncio
async def test_breakdown_by_pii_category(client, seeded_events):
    """by=pii_category: email→2 violations, ssn→1 violation."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/breakdown",
        params={"by": "pii_category", "since": "2026-06-09T00:00:00Z"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["rows"]) == 2
    # Sort is by violations descending; email (2) must be first
    assert body["rows"][0]["key"] == "email"
    rows = {r["key"]: r for r in body["rows"]}
    assert "email" in rows
    assert rows["email"]["violations"] == 2
    assert "ssn" in rows
    assert rows["ssn"]["violations"] == 1


@pytest.mark.asyncio
async def test_breakdown_by_source(client, seeded_events):
    """by=source: safari_extension→11 prompts/1 violation, sdk→5/0."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/breakdown",
        params={"by": "source", "since": "2026-06-09T00:00:00Z"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["rows"]) == 2
    rows = {r["key"]: r for r in body["rows"]}
    assert "safari_extension" in rows
    assert rows["safari_extension"]["prompts"] == 11
    assert rows["safari_extension"]["violations"] == 1
    assert "sdk" in rows
    assert rows["sdk"]["prompts"] == 5
    assert rows["sdk"]["violations"] == 0


@pytest.mark.asyncio
async def test_breakdown_invalid_by_param(client, seeded_events):
    """by=invalid → 422 Unprocessable Entity."""
    resp = await client.get(
        "/api/v1/telemetry/prompt-activity/breakdown",
        params={"by": "invalid"},
        headers=_agg_headers(),
    )
    assert resp.status_code == 422


# ── 4. auth guard ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unauthenticated_request(client):
    """No Authorization header → 401/403."""
    for path in [
        "/api/v1/telemetry/prompt-activity/summary",
        "/api/v1/telemetry/prompt-activity/timeseries",
        "/api/v1/telemetry/prompt-activity/breakdown?by=app",
    ]:
        resp = await client.get(path)
        assert resp.status_code in (401, 403), f"{path}: expected 401/403, got {resp.status_code}"
