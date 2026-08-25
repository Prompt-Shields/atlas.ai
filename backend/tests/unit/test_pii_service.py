"""Unit tests for app.services.pii_service (issue #245).

Covers:
  - detect finds each PII class with correct spans
  - overlap resolution prefers the longest match
  - anonymize is reversible via the returned placeholder map
  - reidentify restores the exact original text
  - clean text produces no spans / an unchanged round-trip
"""

from __future__ import annotations

import itertools

import pytest

from app.services.pii_service import PIIClass, anonymize_text, detect_pii, reidentify_text

pytestmark = [pytest.mark.unit]

EXAMPLES: dict[PIIClass, str] = {
    PIIClass.email: "reach me at jane.doe@example.com for details",
    PIIClass.phone: "call the office at 415-555-0199 today",
    PIIClass.ssn: "SSN on file: 078-05-1120",
    PIIClass.credit_card: "card number 4111 1111 1111 1111 was charged",
    PIIClass.ip_address: "the request came from 192.168.1.42 last night",
    PIIClass.api_key: "use sk-abcdefghijklmnopqrstuvwx1234 as the token",
}


class TestDetect:
    @pytest.mark.parametrize("pii_class", list(EXAMPLES))
    def test_detects_class(self, pii_class: PIIClass) -> None:
        text = EXAMPLES[pii_class]
        spans = detect_pii(text)
        classes = [s.pii_class for s in spans]
        assert pii_class in classes
        span = next(s for s in spans if s.pii_class == pii_class)
        assert text[span.start : span.end] == span.text

    def test_no_pii_returns_no_spans(self) -> None:
        assert detect_pii("just a plain sentence with no secrets") == []

    def test_multiple_classes_in_one_text(self) -> None:
        text = "Email jane.doe@example.com or call 415-555-0199."
        spans = detect_pii(text)
        classes = {s.pii_class for s in spans}
        assert classes == {PIIClass.email, PIIClass.phone}

    def test_credit_card_requires_luhn_validity(self) -> None:
        # 16 digits, but fails the Luhn checksum — must not be flagged.
        spans = detect_pii("bogus number 1234 5678 9012 3456 here")
        assert all(s.pii_class != PIIClass.credit_card for s in spans)

    def test_detected_spans_never_overlap(self) -> None:
        text = "Email jane.doe@example.com, card 4111 1111 1111 1111, ssn 078-05-1120"
        spans = detect_pii(text)
        assert len(spans) == 3
        for a, b in itertools.pairwise(spans):
            assert a.end <= b.start


class TestAnonymizeReidentify:
    def test_anonymize_redacts_all_detected_spans(self) -> None:
        text = "Email jane.doe@example.com or call 415-555-0199."
        redacted, placeholder_map, spans = anonymize_text(text)
        assert "jane.doe@example.com" not in redacted
        assert "415-555-0199" not in redacted
        assert len(placeholder_map) == len(spans) == 2
        for placeholder in placeholder_map:
            assert placeholder in redacted

    def test_reidentify_restores_original_text(self) -> None:
        text = "Email jane.doe@example.com or call 415-555-0199."
        redacted, placeholder_map, _ = anonymize_text(text)
        assert reidentify_text(redacted, placeholder_map) == text

    def test_repeated_class_gets_distinct_placeholders(self) -> None:
        text = "cc jane@example.com and jack@example.com"
        redacted, placeholder_map, spans = anonymize_text(text)
        assert len(spans) == 2
        assert "[EMAIL_1]" in placeholder_map
        assert "[EMAIL_2]" in placeholder_map
        assert reidentify_text(redacted, placeholder_map) == text

    def test_clean_text_round_trips_unchanged(self) -> None:
        text = "nothing sensitive in here"
        redacted, placeholder_map, spans = anonymize_text(text)
        assert redacted == text
        assert placeholder_map == {}
        assert spans == []
        assert reidentify_text(redacted, placeholder_map) == text

    def test_reidentify_ignores_unknown_placeholders(self) -> None:
        assert reidentify_text("no [EMAIL_1] here", {}) == "no [EMAIL_1] here"
