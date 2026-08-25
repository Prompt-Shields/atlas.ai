"""PII detection, anonymization and re-identification (issue #245).

Regex-based detector over a fixed set of common PII classes, plus a
reversible anonymizer: ``anonymize_text`` swaps each detected span for a
per-class placeholder (``[EMAIL_1]``, ``[PHONE_1]``, ...) and returns the
placeholder -> original-text mapping alongside the redacted text.
``reidentify_text`` takes that same map back in and restores the originals.

Nothing here is persisted — the caller (the ``/pii`` router) is stateless
per request, matching the AISPM prototype's "reversible via the returned
map, not a server-side store" behaviour.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class PIIClass(str, Enum):
    email = "EMAIL"
    phone = "PHONE"
    ssn = "SSN"
    credit_card = "CREDIT_CARD"
    ip_address = "IP_ADDRESS"
    api_key = "API_KEY"


@dataclass(frozen=True, slots=True)
class DetectedSpan:
    pii_class: PIIClass
    start: int
    end: int
    text: str
    confidence: float


def _luhn_valid(candidate: str) -> bool:
    """True when the digits in ``candidate`` pass the Luhn checksum (card numbers)."""
    digits = [int(c) for c in candidate if c.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# Ordered by specificity; overlap resolution below prefers the longest match
# regardless of rule order, so this ordering only affects readability.
_RULES: list[tuple[PIIClass, re.Pattern[str], float, Callable[[str], bool] | None]] = [
    (
        PIIClass.email,
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        0.95,
        None,
    ),
    (
        PIIClass.ssn,
        re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
        0.9,
        None,
    ),
    (
        PIIClass.credit_card,
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
        0.85,
        _luhn_valid,
    ),
    (
        PIIClass.phone,
        re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"),
        0.8,
        None,
    ),
    (
        PIIClass.ip_address,
        re.compile(
            r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)"
        ),
        0.75,
        None,
    ),
    (
        PIIClass.api_key,
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
        0.9,
        None,
    ),
]


def detect_pii(text: str) -> list[DetectedSpan]:
    """Scan ``text`` for all configured PII classes, longest match wins on overlap."""
    candidates: list[DetectedSpan] = []
    for pii_class, pattern, confidence, validator in _RULES:
        for m in pattern.finditer(text):
            match_text = m.group(0)
            if validator is not None and not validator(match_text):
                continue
            candidates.append(
                DetectedSpan(
                    pii_class=pii_class,
                    start=m.start(),
                    end=m.end(),
                    text=match_text,
                    confidence=confidence,
                )
            )

    # Resolve overlaps: longest span wins, ties broken by earliest start.
    candidates.sort(key=lambda c: (-(c.end - c.start), c.start))
    occupied: list[tuple[int, int]] = []
    spans: list[DetectedSpan] = []
    for c in candidates:
        if any(c.start < o_end and c.end > o_start for o_start, o_end in occupied):
            continue
        occupied.append((c.start, c.end))
        spans.append(c)

    spans.sort(key=lambda s: s.start)
    return spans


def anonymize_text(text: str) -> tuple[str, dict[str, str], list[DetectedSpan]]:
    """Redact every detected PII span, returning the redacted text and the reversal map."""
    spans = detect_pii(text)
    placeholder_map: dict[str, str] = {}
    counts: dict[PIIClass, int] = {}
    parts: list[str] = []
    cursor = 0

    for span in spans:
        counts[span.pii_class] = counts.get(span.pii_class, 0) + 1
        placeholder = f"[{span.pii_class.value}_{counts[span.pii_class]}]"
        placeholder_map[placeholder] = span.text
        parts.append(text[cursor : span.start])
        parts.append(placeholder)
        cursor = span.end

    parts.append(text[cursor:])
    return "".join(parts), placeholder_map, spans


def reidentify_text(text: str, placeholder_map: dict[str, str]) -> str:
    """Restore original values in ``text`` for every placeholder present in the map."""
    result = text
    # Longest placeholder first so e.g. "[EMAIL_10]" isn't clobbered by a
    # substring replace of "[EMAIL_1]".
    for placeholder in sorted(placeholder_map, key=len, reverse=True):
        result = result.replace(placeholder, placeholder_map[placeholder])
    return result
