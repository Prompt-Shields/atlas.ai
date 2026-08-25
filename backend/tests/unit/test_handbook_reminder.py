"""Unit tests for app.services.handbook_reminder.

Pure-function tests on compose_handbook_reminder_blocks. The
dispatch orchestrator is integration territory (needs DB +
SlackWorkspace + mocked Slack httpx); the composer is what most
needs verifying because it's user-facing copy.
"""

from __future__ import annotations

import pytest

from app.services.handbook_reminder import compose_handbook_reminder_blocks

pytestmark = [pytest.mark.unit]


class TestComposeHandbookReminderBlocks:
    def test_first_week_welcome_tone(self) -> None:
        blocks = compose_handbook_reminder_blocks(
            user_first_name="L.",
            handbook_version="1.0",
            handbook_url="https://atlas.example.com/dashboard/handbook",
            days_since_signup=3,
        )
        # Welcome tone for users still in their first week
        intro = blocks[0]["text"]["text"]
        assert intro.startswith("Welcome to atlas.")
        assert "L." in intro
        assert "v1.0" in intro

    def test_later_reminder_tone(self) -> None:
        blocks = compose_handbook_reminder_blocks(
            user_first_name="M.",
            handbook_version="1.0",
            handbook_url="https://atlas.example.com/x",
            days_since_signup=21,
        )
        intro = blocks[0]["text"]["text"]
        assert intro.startswith("Quick reminder.")
        assert "Welcome" not in intro

    def test_body_explains_what_atlas_sees(self) -> None:
        blocks = compose_handbook_reminder_blocks(
            user_first_name="X",
            handbook_version="1.0",
            handbook_url="https://x.com",
            days_since_signup=5,
        )
        body = blocks[1]["text"]["text"]
        # The reassurance line — atlas sees hashes, not prompts
        assert "SHA-256" in body
        assert "prompt" in body.lower()

    def test_action_button_url(self) -> None:
        url = "https://acme.atlas.example.com/dashboard/handbook"
        blocks = compose_handbook_reminder_blocks(
            user_first_name="V.",
            handbook_version="2.0",
            handbook_url=url,
            days_since_signup=10,
        )
        actions = blocks[2]
        assert actions["type"] == "actions"
        button = actions["elements"][0]
        assert button["url"] == url
        assert "Read the handbook" in button["text"]["text"]

    def test_context_footer_present(self) -> None:
        blocks = compose_handbook_reminder_blocks(
            user_first_name="R.",
            handbook_version="1.0",
            handbook_url="https://x.com",
            days_since_signup=5,
        )
        footer = blocks[-1]
        assert footer["type"] == "context"
        text = footer["elements"][0]["text"]
        assert "STOP" in text
        assert "atlas" in text.lower()

    def test_version_passes_through(self) -> None:
        for v in ("1.0", "2.5", "1.0-acme"):
            blocks = compose_handbook_reminder_blocks(
                user_first_name="X",
                handbook_version=v,
                handbook_url="https://x.com",
                days_since_signup=5,
            )
            assert f"v{v}" in blocks[0]["text"]["text"]

    def test_block_count_is_consistent(self) -> None:
        """The DM should always be 4 blocks: intro / body / action / context."""
        for days in (1, 7, 30, 365):
            blocks = compose_handbook_reminder_blocks(
                user_first_name="X",
                handbook_version="1.0",
                handbook_url="https://x.com",
                days_since_signup=days,
            )
            assert len(blocks) == 4
            assert blocks[0]["type"] == "section"
            assert blocks[1]["type"] == "section"
            assert blocks[2]["type"] == "actions"
            assert blocks[3]["type"] == "context"
