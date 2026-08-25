"""Integration tests for /api/v1/pii (issue #245).

Covers:
  - all three endpoints require authentication
  - any authenticated role (Viewer included) can call them — stateless, no
    RBAC gating
  - detect / anonymize / reidentify wire up to app.services.pii_service
    correctly end-to-end
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import auth_header

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/pii"


class TestAuth:
    async def test_detect_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(f"{BASE}/detect", json={"text": "hi"})
        assert resp.status_code == 401

    async def test_anonymize_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(f"{BASE}/anonymize", json={"text": "hi"})
        assert resp.status_code == 401

    async def test_reidentify_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(f"{BASE}/reidentify", json={"text": "hi", "placeholder_map": {}})
        assert resp.status_code == 401


class TestDetect:
    async def test_detects_email_and_phone(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(
            f"{BASE}/detect",
            json={"text": "Email jane.doe@example.com or call 415-555-0199."},
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 200
        classes = {s["pii_class"] for s in resp.json()["spans"]}
        assert classes == {"EMAIL", "PHONE"}

    async def test_no_pii_returns_empty_spans(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(
            f"{BASE}/detect",
            json={"text": "a plain sentence"},
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 200
        assert resp.json()["spans"] == []


class TestAnonymizeReidentify:
    async def test_anonymize_then_reidentify_round_trip(
        self, client: AsyncClient, viewer_token: str
    ) -> None:
        original = "Email jane.doe@example.com or call 415-555-0199."

        anon_resp = await client.post(
            f"{BASE}/anonymize",
            json={"text": original},
            headers=auth_header(viewer_token),
        )
        assert anon_resp.status_code == 200
        anon_body = anon_resp.json()
        assert "jane.doe@example.com" not in anon_body["redacted_text"]
        assert "415-555-0199" not in anon_body["redacted_text"]
        assert len(anon_body["placeholder_map"]) == 2

        reid_resp = await client.post(
            f"{BASE}/reidentify",
            json={
                "text": anon_body["redacted_text"],
                "placeholder_map": anon_body["placeholder_map"],
            },
            headers=auth_header(viewer_token),
        )
        assert reid_resp.status_code == 200
        assert reid_resp.json()["text"] == original
