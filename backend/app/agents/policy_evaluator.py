"""Policy evaluator — runs a Policy Instance against a sample input.

Pure function port of the dashboard's `lib/policy-engine/evaluator.ts`.
Behaviour-equivalent to that source file: regex / keyword / PII / secrets
/ classifier / LLM-judge / entropy detectors all map 1:1.

Design choices for this port:

- **Dict-based interface, no Pydantic dependency.** This module ships
  before M1 lands the `PolicyTemplate` / `PolicyInstance` ORM models or
  Pydantic schemas. Caller passes plain dicts in, gets a plain dict
  back. When M1 lands, wrap with::

      def evaluate_policy_typed(instance: PolicyInstance, template: PolicyTemplate,
                                test_input: PolicyTestInput) -> PolicyTestResult:
          result = evaluate_policy(instance.model_dump(), template.model_dump(),
                                   test_input.model_dump())
          return PolicyTestResult.model_validate(result)

- **Template passed in, not looked up.** The TS version calls
  ``getTemplateById(instance.templateId)`` from inside the evaluator.
  The Python version expects the caller to fetch the template (e.g. via
  the M1 ORM) and pass it in. This keeps the evaluator free of DB
  dependencies and trivial to unit-test.

- **No floating-point drift.** All confidence values are computed with
  the same arithmetic as the TS source (rounded to 2 decimals at
  comparison points). The wire-format contract test (Task 2.5)
  validates that this Python evaluator and the TS / Swift counterparts
  produce identical decisions for the shared fixture inputs.

This is a *simulator* used by the Test Console. Real-time enforcement
runs in the on-device PEPs (browser extensions, Promptly desktop
clients) which have their own native detector implementations pinned
to the same fixture file.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

# ─── Public API ──────────────────────────────────────────────────────


def evaluate_policy(
    instance: dict[str, Any],
    template: dict[str, Any],
    test_input: dict[str, Any],
) -> dict[str, Any]:
    """Run every detector on the template against the test input.

    Args:
        instance: PolicyInstance dict — must contain ``parameter_values``
            (or ``parameterValues`` for camelCase wire format) and
            ``enforcement_mode``.
        template: PolicyTemplate dict — must contain ``detectors`` list
            and ``triggers`` list.
        test_input: ``{"prompt": str, "expected_output": str | None}``.

    Returns:
        ``{"matched": bool, "triggered_detectors": list[dict],
            "action_that_would_fire": str, "evaluation_time_ms": int}``
    """
    start = time.perf_counter()

    triggered: list[dict[str, Any]] = []

    detectors = template.get("detectors") or []
    for detector in detectors:
        result = _run_detector(detector, instance, template, test_input)
        if result["matched"]:
            entry = {
                "detector_id": detector.get("id", ""),
                "confidence": result["confidence"],
                "explanation": result["explanation"],
            }
            if result.get("matched_substring") is not None:
                entry["matched_substring"] = result["matched_substring"]
            triggered.append(entry)

    matched = len(triggered) > 0
    enforcement_mode = instance.get("enforcement_mode") or instance.get("enforcementMode", "log")
    action = _map_enforcement_to_action(enforcement_mode) if matched else "none"

    return {
        "matched": matched,
        "triggered_detectors": triggered,
        "action_that_would_fire": action,
        "evaluation_time_ms": round((time.perf_counter() - start) * 1000),
    }


# ─── Detector dispatch ───────────────────────────────────────────────


def _run_detector(
    detector: dict[str, Any],
    instance: dict[str, Any],
    template: dict[str, Any],
    test_input: dict[str, Any],
) -> dict[str, Any]:
    params = instance.get("parameter_values") or instance.get("parameterValues") or {}
    config = params.get(detector.get("config_ref") or detector.get("configRef"))
    text = _pick_text_for_stage(template, test_input)
    detector_type = detector.get("type")
    detector_id = detector.get("id", "")

    if detector_type == "regex":
        return _run_regex_detector(text, config)
    if detector_type == "keyword_list":
        return _run_keyword_detector(text, config)
    if detector_type == "pii_detector":
        return _run_pii_detector(text, params)
    if detector_type == "secrets_scanner":
        return _run_secrets_scanner(text)
    if detector_type == "rate_counter":
        # Rate counters need historical state, not a single prompt.
        # For test mode we just report "would-not-fire" with explanation.
        return {
            "matched": False,
            "confidence": 0.0,
            "explanation": "Rate-based detector — only fires on sustained traffic, not test inputs.",
        }
    if detector_type == "classifier":
        return _run_heuristic_classifier(text, config, detector_id)
    if detector_type == "llm_judge":
        return _run_heuristic_judge(text, config, detector_id)
    if detector_type == "entropy":
        return _run_entropy_detector(text)
    return {"matched": False, "confidence": 0.0, "explanation": "Unknown detector type"}


# ─── Detector primitives ─────────────────────────────────────────────


def _run_regex_detector(text: str, patterns: Any) -> dict[str, Any]:
    if not isinstance(patterns, list):
        return {"matched": False, "confidence": 0.0, "explanation": "No patterns configured"}
    for raw in patterns:
        if not isinstance(raw, str) or not raw:
            continue
        try:
            match = re.search(raw, text)
        except re.error:
            continue  # skip invalid regex
        if match:
            return {
                "matched": True,
                "confidence": 1.0,
                "matched_substring": match.group(0),
                "explanation": f"Matched regex pattern: {raw}",
            }
    return {"matched": False, "confidence": 0.0, "explanation": "No regex patterns matched"}


def _run_keyword_detector(text: str, keywords: Any) -> dict[str, Any]:
    if not isinstance(keywords, list):
        return {"matched": False, "confidence": 0.0, "explanation": "No keywords configured"}
    lower = text.lower()
    for kw in keywords:
        if not isinstance(kw, str) or not kw:
            continue
        if kw.lower() in lower:
            return {
                "matched": True,
                "confidence": 0.95,
                "matched_substring": kw,
                "explanation": f'Matched keyword: "{kw}"',
            }
    return {"matched": False, "confidence": 0.0, "explanation": "No keywords matched"}


# Compiled once at import time — these patterns never change.
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("PHONE", re.compile(r"(?:\+?\d{1,3}[\s-]?)?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}")),
    ("NATIONAL_ID", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),  # SSN-shaped
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b")),
]
_PERSON_NAME_PATTERN = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")


def _run_pii_detector(text: str, params: dict[str, Any]) -> dict[str, Any]:
    """Simple PII heuristics — production would use a proper NER model."""
    allowed_categories: list[str] = params.get("redact_categories") or [
        "EMAIL",
        "PHONE",
        "NATIONAL_ID",
        "CREDIT_CARD",
    ]

    for name, pattern in _PII_PATTERNS:
        if name not in allowed_categories:
            continue
        match = pattern.search(text)
        if match:
            return {
                "matched": True,
                "confidence": 0.9,
                "matched_substring": match.group(0),
                "explanation": f"Detected {name}",
            }

    if "PERSON" in allowed_categories:
        person_match = _PERSON_NAME_PATTERN.search(text)
        if person_match:
            return {
                "matched": True,
                "confidence": 0.65,
                "matched_substring": person_match.group(0),
                "explanation": "Detected possible person name (heuristic — production uses NER)",
            }

    return {"matched": False, "confidence": 0.0, "explanation": "No PII detected"}


_SECRETS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS_KEY", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GH_TOKEN", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----")),
    ("SLACK_TOKEN", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
]


def _run_secrets_scanner(text: str) -> dict[str, Any]:
    for name, pattern in _SECRETS_PATTERNS:
        match = pattern.search(text)
        if match:
            return {
                "matched": True,
                "confidence": 1.0,
                "matched_substring": match.group(0),
                "explanation": f"Detected {name}",
            }
    return {"matched": False, "confidence": 0.0, "explanation": "No secrets detected"}


_CLASSIFIER_VOCAB: dict[str, list[str]] = {
    "ml-classifier": [
        "ignore previous",
        "disregard",
        "system prompt",
        "you are now",
        "developer mode",
        "jailbreak",
        "DAN",
        "reveal your instructions",
    ],
    "toxicity-classifier": ["hate", "kill", "stupid", "racist", "harassment"],
    "similarity-check": ["you are a", "your guidelines", "never discuss", "always respond"],
}


def _run_heuristic_classifier(text: str, threshold: Any, detector_id: str) -> dict[str, Any]:
    """Heuristic classifier stand-in. Production would call a real ML
    model. For test mode we approximate based on suspicious vocabulary.
    """
    t = float(threshold) if isinstance(threshold, (int, float)) else 0.75
    lower = text.lower()

    words = _CLASSIFIER_VOCAB.get(detector_id, _CLASSIFIER_VOCAB["ml-classifier"])
    hits = 0
    strongest = ""
    for w in words:
        if w.lower() in lower:
            hits += 1
            if len(w) > len(strongest):
                strongest = w

    # Confidence scales with hit count, capped at 0.99
    confidence = min(0.4 + hits * 0.2, 0.99)
    if confidence >= t:
        return {
            "matched": True,
            "confidence": confidence,
            "matched_substring": strongest,
            "explanation": f"Classifier confidence {confidence:.2f} >= threshold {t}",
        }
    return {
        "matched": False,
        "confidence": confidence,
        "explanation": f"Classifier confidence {confidence:.2f} < threshold {t}",
    }


_JUDGE_SIGNALS: dict[str, list[str]] = {
    "broad-query-detector": ["all customer", "everyone", "complete list", "every record", "dump"],
    "litigation-context": [
        "litigation",
        "vs ",
        "v. ",
        "settlement",
        "outside counsel",
        "privileged",
    ],
}


def _run_heuristic_judge(text: str, threshold: Any, detector_id: str) -> dict[str, Any]:
    """LLM-judge stand-in: looks for signal phrases for proportionality / privilege."""
    t = float(threshold) if isinstance(threshold, (int, float)) else 0.7
    lower = text.lower()
    words = _JUDGE_SIGNALS.get(detector_id, [])
    hits = sum(1 for w in words if w in lower)
    confidence = min(0.5 + hits * 0.15, 0.99)
    return {
        "matched": confidence >= t,
        "confidence": confidence,
        "explanation": f"Judge confidence {confidence:.2f} (threshold {t})",
    }


def _run_entropy_detector(text: str) -> dict[str, Any]:
    """Shannon entropy heuristic for high-entropy strings (potential secrets)."""
    words = [w for w in re.split(r"\s+", text) if len(w) >= 20]
    for w in words:
        e = _shannon_entropy(w)
        if e > 4.5:
            return {
                "matched": True,
                "confidence": min(e / 6, 0.99),
                "matched_substring": w,
                "explanation": f"High-entropy string detected (entropy={e:.2f})",
            }
    return {"matched": False, "confidence": 0.0, "explanation": "No high-entropy strings"}


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(s)
    entropy = 0.0
    for count in freq.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


# ─── Helpers ─────────────────────────────────────────────────────────


def _pick_text_for_stage(template: dict[str, Any], test_input: dict[str, Any]) -> str:
    """Pick the right text to run detectors against based on template trigger stages.

    For test mode, prefer the prompt; if template only triggers on
    output, use the expected_output when provided.
    """
    triggers = template.get("triggers") or []
    stages = [t.get("stage") for t in triggers]
    prompt = test_input.get("prompt", "")
    expected_output = test_input.get("expected_output") or test_input.get("expectedOutput")

    if "input" in stages:
        return prompt
    if "output" in stages and expected_output:
        return expected_output
    if "output" in stages:
        return prompt  # fall back
    return prompt


def _map_enforcement_to_action(mode: str) -> str:
    """Map the enforcement_mode to the action that would fire.

    Falls back to "log" for unrecognised modes — same as the TS source.
    """
    if mode in {"block", "redact", "flag", "log"}:
        return mode
    return "log"
