"""#242 · Unit tests for /api/v1/discover/voice-interview.

Covers:
  - Extraction produces one UseCase draft per employee turn mentioning a
    known AI tool (agent turns and tool-free turns are skipped).
  - Drafts land in the use-case registry as DRAFT / VOICE_INTERVIEW and are
    visible via GET /use-cases.
  - Tenant scoping (tenant_id comes from the caller's JWT).
  - RBAC (Viewer cannot extract; OrgAdmin+ can).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    TEST_TENANT_ID,
    auth_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BASE = "/api/v1/discover/voice-interview"

_TRANSCRIPT = {
    "transcript": [
        {"speaker": "Agent", "role": "agent", "text": "What AI tools do you use day to day?"},
        {
            "speaker": "Priya Shah",
            "role": "employee",
            "text": "I use ChatGPT to draft grant renewal memos.",
            "department": "Grants Management",
        },
        {"speaker": "Agent", "role": "agent", "text": "Anything else?"},
        {
            "speaker": "Priya Shah",
            "role": "employee",
            "text": "Also GitHub Copilot for our internal reporting scripts.",
            "department": "Grants Management",
        },
        {
            "speaker": "Priya Shah",
            "role": "employee",
            "text": "That's about it, nothing else really.",
            "department": "Grants Management",
        },
    ],
    "default_department": "Grants Management",
}


class TestExtract:
    async def test_org_admin_can_extract(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/extract",
            headers=auth_header(tenant_admin_token),
            json=_TRANSCRIPT,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created_count"] == 2
        assert len(body["drafts"]) == 2
        tools = {d["tool"] for d in body["drafts"]}
        assert tools == {"ChatGPT", "GitHub Copilot"}
        for draft in body["drafts"]:
            assert draft["status"] == "DRAFT"
            assert draft["source"] == "VOICE_INTERVIEW"
            assert draft["department"] == "Grants Management"
            assert draft["tenant_id"] == str(TEST_TENANT_ID)

    async def test_agent_turns_and_tool_free_turns_are_skipped(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/extract",
            headers=auth_header(tenant_admin_token),
            json=_TRANSCRIPT,
        )
        assert resp.json()["created_count"] == 2

    async def test_drafts_are_visible_in_the_use_case_registry(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        await client.post(
            f"{BASE}/extract", headers=auth_header(tenant_admin_token), json=_TRANSCRIPT
        )
        listed = await client.get(
            "/api/v1/use-cases",
            headers=auth_header(tenant_admin_token),
            params={"source": "VOICE_INTERVIEW"},
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 2

    async def test_viewer_cannot_extract(self, client: AsyncClient, viewer_token: str) -> None:
        resp = await client.post(
            f"{BASE}/extract", headers=auth_header(viewer_token), json=_TRANSCRIPT
        )
        assert resp.status_code == 403, resp.text

    async def test_no_tool_mentions_yields_no_drafts(
        self, client: AsyncClient, tenant_admin_token: str
    ) -> None:
        resp = await client.post(
            f"{BASE}/extract",
            headers=auth_header(tenant_admin_token),
            json={
                "transcript": [
                    {"speaker": "Agent", "role": "agent", "text": "Hello there."},
                    {"speaker": "Sam", "role": "employee", "text": "Nothing to report."},
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"drafts": [], "created_count": 0}
