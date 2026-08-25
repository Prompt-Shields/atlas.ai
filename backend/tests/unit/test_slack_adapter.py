"""Unit tests for app.services.slack_adapter.

Covers the pure-function half of the adapter — no HTTP I/O:

  - verify_signature: timestamp freshness, HMAC match/mismatch,
    malformed inputs
  - render_question_blocks: shape per question type, action_id
    formatting, optional Skip button
  - parse_interaction: submit / skip / answer_select dispatch,
    extracts value for each Block Kit element type
  - parse_form_payload: form unwrap

The httpx-based slack_post / chat_post_message / conversations_open
functions are smoke-tested with httpx.MockTransport in
test_slack_adapter_http.py (not included in this PR's tight scope —
the pure helpers are the unit boundary).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.services.slack_adapter import (
    InteractionParseError,
    SignatureInvalid,
    parse_form_payload,
    parse_interaction,
    render_consent_block,
    render_question_blocks,
    verify_signature,
)

pytestmark = [pytest.mark.unit]


# ─── verify_signature ────────────────────────────────────────────────


def _sign(secret: str, ts: int, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid_signature_passes(self) -> None:
        secret = "shh-secret"
        ts = int(time.time())
        body = b'{"hello":"world"}'
        sig = _sign(secret, ts, body)
        # Must not raise.
        verify_signature(secret, str(ts), body, sig, now=ts + 1)

    def test_wrong_signature_fails(self) -> None:
        secret = "shh-secret"
        ts = int(time.time())
        body = b'{"hello":"world"}'
        with pytest.raises(SignatureInvalid, match="mismatch"):
            verify_signature(secret, str(ts), body, "v0=deadbeef" * 8, now=ts + 1)

    def test_stale_timestamp_rejected(self) -> None:
        secret = "shh-secret"
        ts = 1_000_000_000
        body = b"{}"
        sig = _sign(secret, ts, body)
        # 1 hour later → outside the 5-minute window.
        with pytest.raises(SignatureInvalid, match="freshness"):
            verify_signature(secret, str(ts), body, sig, now=ts + 3600)

    def test_future_timestamp_rejected(self) -> None:
        secret = "shh-secret"
        ts = 1_000_000_000
        body = b"{}"
        sig = _sign(secret, ts, body)
        # 1 hour in the future → also outside the window.
        with pytest.raises(SignatureInvalid, match="freshness"):
            verify_signature(secret, str(ts), body, sig, now=ts - 3600)

    def test_missing_signature_header(self) -> None:
        with pytest.raises(SignatureInvalid, match="malformed"):
            verify_signature("shh", "1", b"{}", "", now=1)

    def test_malformed_signature_header(self) -> None:
        with pytest.raises(SignatureInvalid, match="malformed"):
            verify_signature("shh", "1", b"{}", "v1=abc", now=1)

    def test_non_integer_timestamp(self) -> None:
        with pytest.raises(SignatureInvalid, match="integer"):
            verify_signature("shh", "abc", b"{}", "v0=def", now=1)

    def test_no_secret_configured(self) -> None:
        with pytest.raises(SignatureInvalid, match="not configured"):
            verify_signature("", "1", b"{}", "v0=def", now=1)


# ─── render_question_blocks ──────────────────────────────────────────


class TestRenderQuestionBlocks:
    def _single_select(self) -> dict:
        return {
            "id": "uses-ai",
            "prompt": "Did you use an AI tool?",
            "type": "singleSelect",
            "options": ["Yes", "No", "Not sure"],
            "optional": False,
        }

    def _multi_select(self) -> dict:
        return {
            "id": "tools",
            "prompt": "Which tools?",
            "type": "multiSelect",
            "options": ["ChatGPT", "Claude"],
            "optional": False,
        }

    def _free_text(self) -> dict:
        return {
            "id": "notes",
            "prompt": "Anything else?",
            "type": "freeText",
            "options": [],
            "optional": True,
        }

    def test_single_select_renders_radio_buttons(self) -> None:
        blocks = render_question_blocks(
            question=self._single_select(),
            response_id="r-abc",
            answered=0,
            total=5,
        )
        # context (progress), section (prompt), actions (radio), actions (buttons)
        assert len(blocks) == 4
        radio = blocks[2]["elements"][0]
        assert radio["type"] == "radio_buttons"
        assert radio["action_id"] == "r-abc::uses-ai"
        assert len(radio["options"]) == 3

    def test_progress_block_format(self) -> None:
        blocks = render_question_blocks(
            question=self._single_select(),
            response_id="r-abc",
            answered=2,
            total=5,
        )
        progress = blocks[0]
        assert "*3*" in progress["elements"][0]["text"]
        assert "of *5*" in progress["elements"][0]["text"]

    def test_multi_select_renders_checkboxes(self) -> None:
        blocks = render_question_blocks(
            question=self._multi_select(),
            response_id="r-abc",
            answered=1,
            total=5,
        )
        checkbox = blocks[2]["elements"][0]
        assert checkbox["type"] == "checkboxes"

    def test_free_text_renders_input(self) -> None:
        blocks = render_question_blocks(
            question=self._free_text(),
            response_id="r-abc",
            answered=4,
            total=5,
        )
        input_block = blocks[2]
        assert input_block["type"] == "input"
        assert input_block["optional"] is True
        assert input_block["element"]["type"] == "plain_text_input"

    def test_optional_question_has_skip_button(self) -> None:
        blocks = render_question_blocks(
            question=self._free_text(),
            response_id="r-abc",
            answered=4,
            total=5,
        )
        buttons = blocks[3]["elements"]
        action_ids = [b["action_id"] for b in buttons]
        assert any(a.startswith("submit::") for a in action_ids)
        assert any(a.startswith("skip::") for a in action_ids)

    def test_required_question_has_no_skip(self) -> None:
        blocks = render_question_blocks(
            question=self._single_select(),
            response_id="r-abc",
            answered=0,
            total=5,
        )
        buttons = blocks[3]["elements"]
        action_ids = [b["action_id"] for b in buttons]
        assert any(a.startswith("submit::") for a in action_ids)
        assert not any(a.startswith("skip::") for a in action_ids)

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            render_question_blocks(
                question={"id": "x", "prompt": "x", "type": "weird"},
                response_id="r",
                answered=0,
                total=1,
            )

    def test_consent_block_has_tenant_name(self) -> None:
        block = render_consent_block("Sample Foundation")
        assert "Sample Foundation" in block["text"]["text"]
        assert "STOP" in block["text"]["text"]


# ─── parse_interaction ───────────────────────────────────────────────


class TestParseInteraction:
    def test_radio_button_submit(self) -> None:
        """Submit button click after a radio button choice."""
        payload = {
            "user": {"id": "U123"},
            "team": {"id": "T456"},
            "actions": [
                {
                    "type": "button",
                    "action_id": "submit::resp-abc::uses-ai",
                    "value": "uses-ai",
                }
            ],
            # The radio selection lives in `state.values` for buttons,
            # but for our shape the value comes from the button's
            # adjacent input — radio buttons' selected value comes via
            # answer_select. The submit handler in the router only
            # reads value for submit when the element is a plain_text
            # input. For radio submit, the action_select event already
            # advanced state; submit just confirms.
            "state": {"values": {}},
        }
        parsed = parse_interaction(payload)
        assert parsed["action_type"] == "submit"
        assert parsed["response_id"] == "resp-abc"
        assert parsed["question_id"] == "uses-ai"

    def test_radio_button_selection_answer_select(self) -> None:
        payload = {
            "actions": [
                {
                    "type": "radio_buttons",
                    "action_id": "resp-abc::uses-ai",
                    "selected_option": {"value": "Yes"},
                }
            ],
        }
        parsed = parse_interaction(payload)
        assert parsed["action_type"] == "answer_select"
        assert parsed["response_id"] == "resp-abc"
        assert parsed["question_id"] == "uses-ai"
        assert parsed["value"] == "Yes"

    def test_checkbox_multi_select(self) -> None:
        payload = {
            "actions": [
                {
                    "type": "checkboxes",
                    "action_id": "resp-abc::tools",
                    "selected_options": [
                        {"value": "ChatGPT"},
                        {"value": "Claude"},
                    ],
                }
            ],
        }
        parsed = parse_interaction(payload)
        assert parsed["action_type"] == "answer_select"
        assert parsed["value"] == ["ChatGPT", "Claude"]

    def test_plain_text_submit_reads_state(self) -> None:
        payload = {
            "actions": [
                {
                    "type": "button",
                    "action_id": "submit::resp-abc::notes",
                    "value": "notes",
                }
            ],
            "state": {
                "values": {"block-notes": {"resp-abc::notes": {"value": "  weekly grant memo  "}}}
            },
        }
        parsed = parse_interaction(payload)
        assert parsed["action_type"] == "submit"
        assert parsed["value"] == "  weekly grant memo  "

    def test_skip_button(self) -> None:
        payload = {
            "actions": [
                {
                    "type": "button",
                    "action_id": "skip::resp-abc::notes",
                    "value": "notes",
                }
            ],
        }
        parsed = parse_interaction(payload)
        assert parsed["action_type"] == "skip"
        assert parsed["question_id"] == "notes"
        assert parsed["value"] is None

    def test_empty_actions_raises(self) -> None:
        with pytest.raises(InteractionParseError, match="actions"):
            parse_interaction({"actions": []})

    def test_missing_separator_raises(self) -> None:
        with pytest.raises(InteractionParseError, match="separator"):
            parse_interaction({"actions": [{"type": "button", "action_id": "no_separator"}]})

    def test_user_and_team_extracted(self) -> None:
        payload = {
            "user": {"id": "U001"},
            "team": {"id": "T002"},
            "actions": [
                {
                    "type": "radio_buttons",
                    "action_id": "r1::q1",
                    "selected_option": {"value": "Yes"},
                }
            ],
        }
        parsed = parse_interaction(payload)
        assert parsed["user_id"] == "U001"
        assert parsed["team_id"] == "T002"


# ─── parse_form_payload ──────────────────────────────────────────────


class TestParseFormPayload:
    def test_unwraps_payload_field(self) -> None:
        inner = {"actions": [{"action_id": "test"}]}
        encoded = urlencode({"payload": json.dumps(inner)}).encode("utf-8")
        result = parse_form_payload(encoded)
        assert result == inner

    def test_missing_payload_raises(self) -> None:
        with pytest.raises(InteractionParseError, match="missing 'payload'"):
            parse_form_payload(b"foo=bar")

    def test_invalid_json_raises(self) -> None:
        encoded = urlencode({"payload": "not-json{"}).encode("utf-8")
        with pytest.raises(InteractionParseError, match="not valid JSON"):
            parse_form_payload(encoded)
