"""Seeded Microsoft Sentinel event generator (issue #247).

v1 registers Sentinel in the integrations catalog and stores connect
config (workspace name, target table, mapped event types) but makes no
live call to Azure Monitor's Logs Ingestion API — see
docs/feedbacks/integrations/microsoft-sentinel/spec.md for the real
ingestion design (forwarder + DCR/DCE), which is out of scope here.

This module produces a deterministic set of synthetic events shaped
like the `PromptShieldsActivity_CL` custom table (see
docs/feedbacks/integrations/microsoft-sentinel/data-schema.md),
filtered to whichever event types the tenant mapped during connect.
Nothing here is persisted; the "seeded event generator" wording in the
issue matches the AISPM prototype's pure front-end mock.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any


class SentinelEventType(str, Enum):
    redacted = "Redacted"
    anonymised = "Anonymised"
    blocked = "Blocked"
    coached = "Coached"
    bias_flagged = "BiasFlagged"


class SentinelSeverity(str, Enum):
    low = "Low"
    medium = "Medium"
    high = "High"


def _prompt_hash(seed: str) -> str:
    """Stand-in for the SHA-256 PromptHash column — never the prompt body."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


# Deterministic seed data, one entry per SentinelEventType so a tenant
# mapping a subset of types sees a proportional stream. Shape matches
# data-schema.md's PromptShieldsActivity_CL columns.
_SEED_EVENTS: list[dict[str, Any]] = [
    {
        "offset_minutes": 4,
        "user": "l.park@example.org",
        "ai_tool": "ChatGPT Business",
        "is_shadow_ai": False,
        "event_type": SentinelEventType.redacted,
        "sensitive_type": "SSN+EIN",
        "severity": SentinelSeverity.medium,
        "detail": "Pasted grantee SSN + EIN drafting MOU",
    },
    {
        "offset_minutes": 11,
        "user": "a.morgan@example.org",
        "ai_tool": "Claude",
        "is_shadow_ai": False,
        "event_type": SentinelEventType.anonymised,
        "sensitive_type": "PHI",
        "severity": SentinelSeverity.medium,
        "detail": "Anonymised patient intake notes before summarisation",
    },
    {
        "offset_minutes": 23,
        "user": "j.rivera@example.org",
        "ai_tool": "Gemini",
        "is_shadow_ai": True,
        "event_type": SentinelEventType.blocked,
        "sensitive_type": "Banking",
        "severity": SentinelSeverity.high,
        "detail": "Blocked wire-transfer instructions pasted into unsanctioned tool",
    },
    {
        "offset_minutes": 34,
        "user": "s.chen@example.org",
        "ai_tool": "Microsoft Copilot Premium",
        "is_shadow_ai": False,
        "event_type": SentinelEventType.coached,
        "sensitive_type": None,
        "severity": SentinelSeverity.low,
        "detail": "Coached on internal-only data-handling policy before send",
    },
    {
        "offset_minutes": 47,
        "user": "d.osei@example.org",
        "ai_tool": "ChatGPT Business",
        "is_shadow_ai": False,
        "event_type": SentinelEventType.bias_flagged,
        "sensitive_type": "Protected characteristics",
        "severity": SentinelSeverity.high,
        "detail": "Flagged candidate-screening prompt referencing protected characteristics",
    },
    {
        "offset_minutes": 58,
        "user": "m.kowalski@example.org",
        "ai_tool": "Perplexity",
        "is_shadow_ai": True,
        "event_type": SentinelEventType.blocked,
        "sensitive_type": "Donor list",
        "severity": SentinelSeverity.high,
        "detail": "Blocked donor list export to unsanctioned tool",
    },
    {
        "offset_minutes": 72,
        "user": "l.park@example.org",
        "ai_tool": "Claude",
        "is_shadow_ai": False,
        "event_type": SentinelEventType.redacted,
        "sensitive_type": "Compensation",
        "severity": SentinelSeverity.low,
        "detail": "Redacted salary figures from a benchmarking prompt",
    },
    {
        "offset_minutes": 90,
        "user": "a.morgan@example.org",
        "ai_tool": "Microsoft Copilot Premium",
        "is_shadow_ai": False,
        "event_type": SentinelEventType.coached,
        "sensitive_type": None,
        "severity": SentinelSeverity.low,
        "detail": "Coached on grant-memo handling before drafting continued",
    },
]


def generate_events(*, enabled_event_types: list[SentinelEventType]) -> list[dict[str, Any]]:
    """Seeded events filtered to ``enabled_event_types``, newest first."""
    enabled = set(enabled_event_types)
    now = datetime.now(UTC)
    events: list[dict[str, Any]] = []
    for idx, seed in enumerate(_SEED_EVENTS, start=1):
        if seed["event_type"] not in enabled:
            continue
        events.append(
            {
                "time_generated": now - timedelta(minutes=seed["offset_minutes"]),
                "event_id": f"EV-{1000 + idx}",
                "user": seed["user"],
                "ai_tool": seed["ai_tool"],
                "is_shadow_ai": seed["is_shadow_ai"],
                "event_type": seed["event_type"],
                "sensitive_type": seed["sensitive_type"],
                "severity": seed["severity"],
                "detail": seed["detail"],
                "prompt_hash": _prompt_hash(f"{seed['user']}:{seed['detail']}"),
            }
        )
    events.sort(key=lambda e: e["time_generated"], reverse=True)
    return events
