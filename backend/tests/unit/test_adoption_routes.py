"""Integration tests for /api/v1/adoption (issue #249).

Covers:
  - the endpoint requires authentication
  - any authenticated role (Viewer included) can call it — stateless, no
    RBAC gating, matching /pii
  - response shape: headline + all 4 quality dimensions present
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/adoption"


class TestAuth:
    async def test_usage_quality_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/usage-quality")
        assert resp.status_code == 401


class TestUsageQuality:
    async def test_returns_headline_and_all_dimensions(
        self, client: AsyncClient, viewer_token: str
    ) -> None:
        resp = await client.get(f"{BASE}/usage-quality", headers=auth_header(viewer_token))
        assert resp.status_code == 200
        body = resp.json()

        headline = body["headline"]
        assert headline["roi_multiplier"] > 0
        assert headline["hours_saved_per_week"] > 0
        assert 0 <= headline["upskilling_score"] <= 100
        assert headline["window_days"] > 0

        keys = {d["key"] for d in body["dimensions"]}
        assert keys == {"outcome", "leverage", "trajectory", "diffusion"}
        for dimension in body["dimensions"]:
            assert 0 <= dimension["score"] <= 100
            assert dimension["narrative"]
