"""Unit tests for app.services.reattestation_reminder.

Pure-function tests on compose_reminder_blocks — the Block Kit
payload sent to Slack. The dispatch_overdue_reminders orchestrator
is integration territory (requires SlackWorkspace + User rows +
mocked httpx); the composer is what most needs verifying because
it's user-facing copy and Block Kit shape.
"""

from __future__ import annotations

import pytest

from app.services.reattestation_reminder import compose_reminder_blocks

pytestmark = [pytest.mark.unit]


class TestComposeReminderBlocks:
    def test_single_overdue_use_case(self) -> None:
        blocks = compose_reminder_blocks(
            user_first_name="L.",
            overdue_count=1,
            sample_titles=["Grant memo drafting"],
            dashboard_url="https://atlas.example.com/dashboard/reattestation?status=OVERDUE",
        )
        # First block is the greeting
        assert blocks[0]["type"] == "section"
        assert "L." in blocks[0]["text"]["text"]
        assert "1 AI use case" in blocks[0]["text"]["text"]
        # "is" (singular)
        assert "is overdue" in blocks[0]["text"]["text"]

        # Second block: body mentions the title
        body_text = blocks[1]["text"]["text"]
        assert "Grant memo drafting" in body_text

        # Third block: action button with the right URL + label
        actions = blocks[2]
        assert actions["type"] == "actions"
        button = actions["elements"][0]
        assert button["url"].endswith("status=OVERDUE")
        assert "Review now" in button["text"]["text"]

    def test_multiple_overdue_use_cases(self) -> None:
        blocks = compose_reminder_blocks(
            user_first_name="M.",
            overdue_count=3,
            sample_titles=[
                "Grant memo drafting",
                "Press release first-draft",
                "990-PF narrative assist",
            ],
            dashboard_url="https://atlas.example.com/x",
        )
        # Plural "are overdue"
        assert "are overdue" in blocks[0]["text"]["text"]
        assert "3 AI use cases" in blocks[0]["text"]["text"]

        # Body should mention all three titles (≤3 case)
        body = blocks[1]["text"]["text"]
        assert "Grant memo drafting" in body
        assert "Press release first-draft" in body
        assert "990-PF narrative assist" in body

        # Button label includes count
        assert "Review 3 now" in blocks[2]["elements"][0]["text"]["text"]

    def test_many_overdue_use_cases(self) -> None:
        """When >3 titles known, show 2 + 'N more'."""
        blocks = compose_reminder_blocks(
            user_first_name="S.",
            overdue_count=5,
            sample_titles=[
                "Grant memo drafting",
                "Press release first-draft",
                "990-PF narrative assist",
            ],
            dashboard_url="https://atlas.example.com/x",
        )
        body = blocks[1]["text"]["text"]
        assert "Grant memo drafting" in body
        assert "Press release first-draft" in body
        # 990-PF should NOT appear; should be summarised as "and N more"
        assert "990-PF narrative assist" not in body
        assert "and 3 more" in body

    def test_no_titles_fallback(self) -> None:
        """Defensive: composer doesn't blow up if sample_titles is empty."""
        blocks = compose_reminder_blocks(
            user_first_name="V.",
            overdue_count=1,
            sample_titles=[],
            dashboard_url="https://atlas.example.com/x",
        )
        # Should still produce a 4-block message
        assert len(blocks) == 4
        # Body falls back to a placeholder rather than crashing
        body = blocks[1]["text"]["text"]
        assert "atlas" in body.lower() or "use case" in body.lower()

    def test_context_footer_present(self) -> None:
        """Every message must include the lock + STOP footer."""
        blocks = compose_reminder_blocks(
            user_first_name="R.",
            overdue_count=2,
            sample_titles=["Use A", "Use B"],
            dashboard_url="https://x.com",
        )
        # The last block should be the context footer
        footer = blocks[-1]
        assert footer["type"] == "context"
        text = footer["elements"][0]["text"]
        assert "STOP" in text
        assert "Atlas" in text or "atlas" in text.lower()

    def test_dashboard_url_passes_through(self) -> None:
        url = "https://acme.atlas.example.com/dashboard/reattestation?status=OVERDUE&owner=me"
        blocks = compose_reminder_blocks(
            user_first_name="X",
            overdue_count=1,
            sample_titles=["x"],
            dashboard_url=url,
        )
        actions = next(b for b in blocks if b["type"] == "actions")
        assert actions["elements"][0]["url"] == url
