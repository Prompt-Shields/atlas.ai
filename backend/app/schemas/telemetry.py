"""Wire-format schemas for /api/v1/telemetry — prompt telemetry from
the prompt-shields clients (Safari extension, macOS widget, SDK).

PRIVACY CONTRACT: there is no prompt-text field and `extra="forbid"`
refuses any unknown field, so raw prompt content can never be stored
even by a buggy client. Hash + metadata only.

The enum values are wire contracts with three client repos — see
docs/superpowers/specs/2026-06-11-prompt-telemetry-design.md before
changing anything here.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PromptEventSource(str, enum.Enum):
    SAFARI_EXTENSION = "safari_extension"
    MACOS_WIDGET = "macos_widget"
    SDK = "sdk"


class PromptEventKind(str, enum.Enum):
    ACTIVITY = "activity"
    VIOLATION = "violation"


class PromptEventAction(str, enum.Enum):
    ALLOWED = "allowed"
    LOGGED = "logged"  # Safari actionTaken vocabulary — observed, not acted on
    REDACTED = "redacted"
    FLAGGED = "flagged"
    BLOCKED = "blocked"


class PromptEventSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"  # sent by macOS PolicySeverity


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PromptEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: PromptEventSource
    event_kind: PromptEventKind
    app_id: str | None = Field(None, max_length=120)
    prompt_hash: str | None = None
    action: PromptEventAction | None = None
    severity: PromptEventSeverity | None = None
    pii_categories: dict[str, int] = Field(default_factory=dict)
    device_fingerprint: str | None = Field(None, max_length=100)
    user_external_id: str | None = Field(None, max_length=255)
    session_id: str | None = Field(None, max_length=120)
    vendor: str | None = Field(None, max_length=50)
    model: str | None = Field(None, max_length=120)
    tokens_in: int | None = Field(None, ge=0)
    tokens_out: int | None = Field(None, ge=0)
    estimated_cost_usd: float | None = Field(None, ge=0)
    occurrences: int = Field(1, ge=1, le=10_000)
    occurred_at: datetime | None = None

    @field_validator("prompt_hash")
    @classmethod
    def _validate_hash(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.lower()
        if not _HASH_RE.match(v):
            raise ValueError("prompt_hash must be exactly 64 hex characters")
        return v

    @field_validator("pii_categories")
    @classmethod
    def _validate_categories(cls, v: dict[str, int]) -> dict[str, int]:
        for key, count in v.items():
            if not isinstance(count, int) or count < 1:
                raise ValueError(f"pii_categories[{key!r}] must be a positive integer")
            if len(key) > 100:
                raise ValueError("pii_categories keys must be <= 100 chars")
        return v


class PromptEventIngestResponse(BaseModel):
    ingested: int
    skipped: int
    skipped_reasons: list[str]


# ── Aggregate responses (dashboard) ──────────────────────────────────


class PromptActivitySummary(BaseModel):
    total_prompts: int
    total_violations: int
    violation_rate: float  # 0.0–1.0; 0.0 when no prompts
    active_devices: int
    active_sources: int


class PromptActivityBucket(BaseModel):
    date: str  # YYYY-MM-DD
    prompts: int
    violations: int


class PromptActivityTimeseries(BaseModel):
    buckets: list[PromptActivityBucket]


class PromptActivityBreakdownRow(BaseModel):
    key: str
    prompts: int
    violations: int


class PromptActivityBreakdown(BaseModel):
    by: str  # "app" | "pii_category" | "source"
    rows: list[PromptActivityBreakdownRow]
