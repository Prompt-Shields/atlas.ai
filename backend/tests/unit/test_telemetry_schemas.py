"""Unit tests for app.schemas.telemetry — the wire contract with the
three prompt-shields clients. These values are contracts; changing
them breaks Safari/macOS/SDK senders."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.telemetry import (
    PromptEventAction,
    PromptEventIn,
    PromptEventKind,
    PromptEventSeverity,
    PromptEventSource,
)

pytestmark = [pytest.mark.unit]

VALID_HASH = "a" * 64


def make_event(**overrides):
    base = {"source": "safari_extension", "event_kind": "violation"}
    base.update(overrides)
    return PromptEventIn.model_validate(base)


class TestEnumContracts:
    def test_sources(self):
        assert {s.value for s in PromptEventSource} == {
            "safari_extension",
            "macos_widget",
            "sdk",
        }

    def test_kinds(self):
        assert {k.value for k in PromptEventKind} == {"activity", "violation"}

    def test_actions_include_logged(self):
        # 'logged' is the Safari reporter's existing actionTaken value —
        # spec round-1 review caught its omission. Do not remove.
        assert {a.value for a in PromptEventAction} == {
            "allowed",
            "logged",
            "redacted",
            "flagged",
            "blocked",
        }

    def test_severities_include_critical(self):
        # macOS PolicySeverity sends 'critical' — spec round-2 review
        # caught its omission. Do not remove.
        assert {s.value for s in PromptEventSeverity} == {
            "low",
            "medium",
            "high",
            "critical",
        }


class TestPromptEventIn:
    def test_minimal_event(self):
        ev = make_event()
        assert ev.occurrences == 1
        assert ev.pii_categories == {}
        assert ev.prompt_hash is None

    def test_full_violation_event(self):
        ev = make_event(
            prompt_hash=VALID_HASH,
            action="logged",
            severity="critical",
            app_id="chatgpt",
            pii_categories={"email": 2, "ssn": 1},
            device_fingerprint="d" * 36,
            user_external_id="auth0|abc",
            session_id="sess-1",
            occurrences=5,
        )
        assert ev.pii_categories["email"] == 2

    def test_sdk_fields(self):
        ev = make_event(
            source="sdk",
            event_kind="activity",
            action="allowed",
            vendor="openai",
            model="gpt-4o",
            tokens_in=100,
            tokens_out=20,
            estimated_cost_usd=0.0042,
        )
        assert ev.vendor == "openai"

    def test_prompt_hash_uppercase_normalised(self):
        ev = make_event(prompt_hash="A" * 64)
        assert ev.prompt_hash == "a" * 64

    def test_prompt_hash_wrong_length_rejected(self):
        with pytest.raises(ValidationError):
            make_event(prompt_hash="abc123")

    def test_prompt_hash_non_hex_rejected(self):
        with pytest.raises(ValidationError):
            make_event(prompt_hash="z" * 64)

    def test_prompt_text_rejected(self):
        # Privacy guarantee is structural: unknown fields are refused,
        # so a client accidentally sending prompt text cannot store it.
        with pytest.raises(ValidationError):
            make_event(prompt_text="my secret prompt")

    def test_occurrences_bounds(self):
        with pytest.raises(ValidationError):
            make_event(occurrences=0)
        with pytest.raises(ValidationError):
            make_event(occurrences=10_001)

    def test_pii_category_counts_must_be_positive_ints(self):
        with pytest.raises(ValidationError):
            make_event(pii_categories={"email": -1})
