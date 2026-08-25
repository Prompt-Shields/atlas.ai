"""Unit tests for inline Slack reminder action plumbing.

Three concerns covered here:

1. The reminder-action parser in slack_adapter — does it correctly
   extract verb/target/clicker from a Block Kit interaction payload,
   and does it cleanly reject malformed input?
2. The reminder-action confirmation Block Kit composers — do they
   produce the expected replace_original payloads?
3. The action_id wiring in the composers — do reattestation_reminder
   and handbook_reminder emit action_ids the parser can parse?

The end-to-end handler (handle_reminder_action) needs a DB session +
mocked httpx + SlackWorkspace fixture, which is integration territory
and lives in tests/integration. The pure pieces are what most need
verifying — and what is most likely to silently drift between the
composer and the parser.
"""

from __future__ import annotations

import pytest

from app.services.handbook_reminder import compose_handbook_reminder_blocks
from app.services.reattestation_reminder import compose_reminder_blocks
from app.services.reminder_actions import (
    _confirm_failure,
    _confirm_handbook_ack,
    _confirm_handbook_snooze,
    _confirm_review_done,
    _confirm_review_snooze,
)
from app.services.slack_adapter import (
    InteractionParseError,
    is_reminder_action,
    parse_reminder_interaction,
)

pytestmark = [pytest.mark.unit]


# ─── Helpers ─────────────────────────────────────────────────────────


def _wrap(action_id: str, *, value: str | None = None) -> dict:
    """Build a minimal Slack block-actions payload around one button click."""
    action: dict = {"action_id": action_id, "type": "button"}
    if value is not None:
        action["value"] = value
    return {
        "type": "block_actions",
        "user": {"id": "U_SLACK_USER"},
        "team": {"id": "T_TEAM"},
        "response_url": "https://hooks.slack.com/actions/T_TEAM/123/abc",
        "actions": [action],
    }


# ─── Parser: happy paths ─────────────────────────────────────────────


class TestParseReminderInteraction:
    def test_review_done(self) -> None:
        payload = _wrap(
            "rmd_review_done::11111111-1111-1111-1111-111111111111",
            value="11111111-1111-1111-1111-111111111111",
        )
        assert is_reminder_action(payload)
        parsed = parse_reminder_interaction(payload)
        assert parsed["verb"] == "review_done"
        assert parsed["target"] == "11111111-1111-1111-1111-111111111111"
        assert parsed["slack_user_id"] == "U_SLACK_USER"
        assert parsed["team_id"] == "T_TEAM"
        assert parsed["response_url"].startswith("https://hooks.slack.com/")

    def test_review_snooze(self) -> None:
        payload = _wrap("rmd_review_snooze::abc-123")
        parsed = parse_reminder_interaction(payload)
        assert parsed["verb"] == "review_snooze"
        assert parsed["target"] == "abc-123"

    def test_handbook_ack(self) -> None:
        payload = _wrap("rmd_handbook_ack::1.0")
        parsed = parse_reminder_interaction(payload)
        assert parsed["verb"] == "handbook_ack"
        assert parsed["target"] == "1.0"

    def test_handbook_snooze_with_complex_version(self) -> None:
        """Version strings can contain dashes (e.g. tenant-customized handbook)."""
        payload = _wrap("rmd_handbook_snooze::2.1-acme")
        parsed = parse_reminder_interaction(payload)
        assert parsed["verb"] == "handbook_snooze"
        assert parsed["target"] == "2.1-acme"


# ─── Parser: error paths ─────────────────────────────────────────────


class TestParseReminderInteractionErrors:
    def test_not_a_reminder_action(self) -> None:
        # Survey action_ids should not be classified as reminder actions
        payload = _wrap("submit::response_id::question_id")
        assert not is_reminder_action(payload)
        with pytest.raises(InteractionParseError, match="not a reminder action"):
            parse_reminder_interaction(payload)

    def test_missing_separator(self) -> None:
        payload = _wrap("rmd_review_done")
        with pytest.raises(InteractionParseError, match="missing :: separator"):
            parse_reminder_interaction(payload)

    def test_unknown_verb(self) -> None:
        payload = _wrap("rmd_delete_everything::target")
        with pytest.raises(InteractionParseError, match="unknown reminder verb"):
            parse_reminder_interaction(payload)

    def test_empty_target(self) -> None:
        payload = _wrap("rmd_review_done::")
        with pytest.raises(InteractionParseError, match="missing target"):
            parse_reminder_interaction(payload)

    def test_no_actions(self) -> None:
        payload = {
            "type": "block_actions",
            "user": {"id": "U"},
            "team": {"id": "T"},
            "actions": [],
        }
        assert not is_reminder_action(payload)
        with pytest.raises(InteractionParseError, match=r"actions.* missing or empty"):
            parse_reminder_interaction(payload)


# ─── Composer wiring: ids emitted by composers parse cleanly ─────────


class TestComposerActionIdRoundTrip:
    """The point of these is to catch composer/parser drift.

    If anyone changes the action_id format in one place, this test
    breaks loudly instead of silently producing un-routable buttons.
    """

    def test_reattestation_single_review_emits_parseable_ids(self) -> None:
        review_id = "33333333-3333-3333-3333-333333333333"
        blocks = compose_reminder_blocks(
            user_first_name="L.",
            overdue_count=1,
            sample_titles=["Grant memo drafting"],
            dashboard_url="https://x.com",
            single_review_id=review_id,
        )
        actions = blocks[2]
        # Three buttons: primary URL, mark done, snooze
        assert len(actions["elements"]) == 3
        # The URL button has no action_id (it's a link)
        assert "action_id" not in actions["elements"][0]
        # The two inline buttons must be parseable
        for el in actions["elements"][1:]:
            payload = _wrap(el["action_id"])
            parsed = parse_reminder_interaction(payload)
            assert parsed["target"] == review_id
            assert parsed["verb"] in {"review_done", "review_snooze"}

    def test_reattestation_multi_review_omits_inline_buttons(self) -> None:
        """Bulk attestation = bad governance; only the dashboard CTA shows."""
        blocks = compose_reminder_blocks(
            user_first_name="L.",
            overdue_count=3,
            sample_titles=["A", "B", "C"],
            dashboard_url="https://x.com",
            single_review_id=None,
        )
        actions = blocks[2]
        assert len(actions["elements"]) == 1
        # No action_ids on the URL button — it's pure navigation
        assert "action_id" not in actions["elements"][0]

    def test_handbook_emits_parseable_ids(self) -> None:
        blocks = compose_handbook_reminder_blocks(
            user_first_name="L.",
            handbook_version="1.0",
            handbook_url="https://x.com/handbook",
            days_since_signup=5,
        )
        actions = blocks[2]
        # Three buttons: URL, ack, snooze
        assert len(actions["elements"]) == 3
        ack_btn = actions["elements"][1]
        snooze_btn = actions["elements"][2]

        ack_parsed = parse_reminder_interaction(_wrap(ack_btn["action_id"]))
        assert ack_parsed["verb"] == "handbook_ack"
        assert ack_parsed["target"] == "1.0"

        snooze_parsed = parse_reminder_interaction(_wrap(snooze_btn["action_id"]))
        assert snooze_parsed["verb"] == "handbook_snooze"
        assert snooze_parsed["target"] == "1.0"

    def test_handbook_versioned_action_ids_pass_through(self) -> None:
        """Tenant-customized version strings (e.g. '1.0-acme') round-trip."""
        blocks = compose_handbook_reminder_blocks(
            user_first_name="L.",
            handbook_version="1.0-acme",
            handbook_url="https://x.com/handbook",
            days_since_signup=5,
        )
        ack_btn = blocks[2]["elements"][1]
        parsed = parse_reminder_interaction(_wrap(ack_btn["action_id"]))
        assert parsed["target"] == "1.0-acme"


# ─── Confirmation Block Kit composers ────────────────────────────────


class TestConfirmationBlocks:
    def test_review_done_confirmation(self) -> None:
        blocks = _confirm_review_done("Grant memo drafting", already=False)
        assert blocks["replace_original"] == "true"
        text = blocks["blocks"][0]["text"]["text"]
        assert "Grant memo drafting" in text
        assert "marked reviewed" in text
        # Audit-trail note in the context block
        context = blocks["blocks"][1]
        assert context["type"] == "context"
        assert "no material changes" in context["elements"][0]["text"]

    def test_review_done_idempotent_message(self) -> None:
        blocks = _confirm_review_done("X", already=True)
        assert "already marked reviewed" in blocks["blocks"][0]["text"]["text"]

    def test_review_snooze_confirmation(self) -> None:
        blocks = _confirm_review_snooze()
        text = blocks["blocks"][0]["text"]["text"]
        assert "Snoozed" in text
        assert "24h" in text

    def test_handbook_ack_confirmation(self) -> None:
        blocks = _confirm_handbook_ack("1.0", already=False)
        text = blocks["blocks"][0]["text"]["text"]
        assert "v1.0" in text
        assert "Acknowledged" in text or "acknowledged" in text.lower()

    def test_handbook_ack_idempotent_message(self) -> None:
        blocks = _confirm_handbook_ack("1.0", already=True)
        assert "Already" in blocks["blocks"][0]["text"]["text"]

    def test_handbook_snooze_confirmation(self) -> None:
        blocks = _confirm_handbook_snooze()
        text = blocks["blocks"][0]["text"]["text"]
        assert "Snoozed" in text
        assert "24h" in text

    def test_failure_is_ephemeral_and_preserves_original(self) -> None:
        """Failure paths must NOT replace_original — user can retry the CTA."""
        blocks = _confirm_failure("Something went wrong.")
        assert blocks["replace_original"] == "false"
        assert blocks["response_type"] == "ephemeral"
        text = blocks["blocks"][0]["text"]["text"]
        assert "Something went wrong" in text
