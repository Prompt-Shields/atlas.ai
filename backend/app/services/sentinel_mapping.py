"""Map ``grc.prompt_events`` rows onto ``PromptShieldsActivity_CL`` rows.

Pure functions only — every lookup the mapping needs (directory user, enrolled
device, the tenant's sanctioned tool list) is passed in as pre-fetched data by
``sentinel_forwarder``. That keeps the mapping exhaustively unit-testable
without a database and keeps the N+1 queries out of the hot loop.

PRIVACY: ``Detail`` is assembled from structured fields only. There is no
prompt text in ``PromptEvent`` to leak — the telemetry model deliberately has
no such column — and nothing here may ever introduce one. See the "Explicit
non-goals" section of data-schema.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.prompt_event import PromptEvent
from app.schemas.telemetry import (
    PromptEventAction,
    PromptEventKind,
    PromptEventSeverity,
)
from app.services.sentinel_service import SentinelEventType, SentinelSeverity

# ─── Tool naming ─────────────────────────────────────────────────────

# `app_id` / `vendor` on a PromptEvent are client-supplied identifiers
# (hostnames from the Safari extension, bundle-ish ids from the macOS widget).
# data-schema.md keeps `AiTool` free-form so new tools need no schema bump, but
# the common ones get the canonical display names the doc lists so customer KQL
# can group on them reliably.
_TOOL_DISPLAY_NAMES: dict[str, str] = {
    "chatgpt": "ChatGPT Business",
    "chatgpt.com": "ChatGPT Business",
    "chat.openai.com": "ChatGPT Business",
    "openai": "ChatGPT Business",
    "claude": "Claude",
    "claude.ai": "Claude",
    "anthropic": "Claude",
    "gemini": "Gemini",
    "gemini.google.com": "Gemini",
    "bard.google.com": "Gemini",
    "google": "Gemini",
    "perplexity": "Perplexity",
    "perplexity.ai": "Perplexity",
    "copilot": "Microsoft Copilot Premium",
    "copilot.microsoft.com": "Microsoft Copilot Premium",
    "m365.cloud.microsoft": "Microsoft Copilot Premium",
    "microsoft": "Microsoft Copilot Premium",
}

# PII category keys the clients emit that indicate a protected-characteristics
# concern, which promotes a `flagged` action from Coached to BiasFlagged.
_BIAS_CATEGORY_HINTS = (
    "bias",
    "protected",
    "ethnicity",
    "race",
    "religion",
    "gender",
    "age",
    "disability",
    "sexual_orientation",
)

# Human-readable names for the PII category keys the clients emit, so
# `SensitiveType` reads like the doc's examples ("SSN+EIN") rather than like a
# wire key. Unknown keys pass through title-cased.
_SENSITIVE_TYPE_NAMES: dict[str, str] = {
    "ssn": "SSN",
    "social_security_number": "SSN",
    "ein": "EIN",
    "phi": "PHI",
    "pii": "PII",
    "phone": "Phone",
    "email": "Email",
    "address": "Address",
    "credit_card": "Credit card",
    "bank_account": "Banking",
    "banking": "Banking",
    "iban": "Banking",
    "compensation": "Compensation",
    "salary": "Compensation",
    "passport": "Passport",
    "date_of_birth": "Date of birth",
    "api_key": "Credentials",
    "secret": "Credentials",
    "credential": "Credentials",
}

_SEVERITY_MAP: dict[PromptEventSeverity, SentinelSeverity] = {
    PromptEventSeverity.LOW: SentinelSeverity.low,
    PromptEventSeverity.MEDIUM: SentinelSeverity.medium,
    PromptEventSeverity.HIGH: SentinelSeverity.high,
    # data-schema.md's Severity enum has three values; the macOS widget's
    # `critical` is the most severe thing it can say, so it maps to High
    # rather than being dropped.
    PromptEventSeverity.CRITICAL: SentinelSeverity.high,
}

# Fallback severity when the client sent none, keyed by what the event became.
_DEFAULT_SEVERITY: dict[SentinelEventType, SentinelSeverity] = {
    SentinelEventType.blocked: SentinelSeverity.high,
    SentinelEventType.bias_flagged: SentinelSeverity.high,
    SentinelEventType.redacted: SentinelSeverity.medium,
    SentinelEventType.anonymised: SentinelSeverity.medium,
    SentinelEventType.coached: SentinelSeverity.low,
}


@dataclass(frozen=True)
class DirectoryMatch:
    """The directory facts the mapping denormalises onto a row."""

    aad_object_id: str | None = None
    department: str | None = None


@dataclass(frozen=True)
class DeviceMatch:
    """The endpoint facts the mapping denormalises onto a row."""

    endpoint_id: str | None = None
    platform: str | None = None


@dataclass(frozen=True)
class MappingContext:
    """Everything the mapping needs beyond the event itself.

    ``directory_by_user`` and ``devices_by_fingerprint`` are keyed by the same
    normalised forms ``PromptEvent`` stores (lower-cased email / raw
    fingerprint). ``sanctioned_tools`` holds normalised tool names from the
    tenant's ACTIVE use cases — anything outside it is shadow AI.
    """

    directory_by_user: dict[str, DirectoryMatch] = field(default_factory=dict)
    devices_by_fingerprint: dict[str, DeviceMatch] = field(default_factory=dict)
    sanctioned_tools: frozenset[str] = frozenset()


class UnmappableEvent(Exception):
    """The event has no ``PromptShieldsActivity_CL`` representation.

    Carries a short, loggable ``reason``. Raised rather than returning None so
    the caller is forced to record why an event was passed over — silent drops
    are exactly what the spec's audit guarantee forbids.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ─── Field derivations ───────────────────────────────────────────────


def normalise_tool_key(value: str | None) -> str:
    """Lower-case, strip a leading ``www.``, drop surrounding whitespace."""
    if not value:
        return ""
    key = value.strip().lower()
    return key[4:] if key.startswith("www.") else key


def display_tool_name(event: PromptEvent) -> str:
    """Best available human name for the AI tool behind this event.

    Prefers ``app_id`` (what the client actually saw) over ``vendor``. Falls
    back to the raw identifier so a tool we have no mapping for still lands in
    Sentinel under a stable name rather than "Unknown".
    """
    for candidate in (event.app_id, event.vendor):
        key = normalise_tool_key(candidate)
        if not key:
            continue
        if key in _TOOL_DISPLAY_NAMES:
            return _TOOL_DISPLAY_NAMES[key]
        return candidate.strip()  # type: ignore[union-attr]  # non-empty by key check
    return "Unknown"


def is_shadow_ai(event: PromptEvent, sanctioned_tools: frozenset[str]) -> bool:
    """True when no ACTIVE use case in the tenant covers this tool.

    Matching is on the normalised tool key *and* on the canonical display name,
    so a use case registered as "ChatGPT Business" still sanctions an event the
    Safari extension reported as ``chatgpt.com``.
    """
    candidates = {
        normalise_tool_key(event.app_id),
        normalise_tool_key(event.vendor),
        normalise_tool_key(display_tool_name(event)),
    }
    candidates.discard("")
    if not candidates:
        # Nothing identifies the tool, so nothing can sanction it.
        return True
    return not (candidates & sanctioned_tools)


def _has_bias_signal(pii_categories: dict[str, Any]) -> bool:
    return any(hint in key.lower() for key in pii_categories for hint in _BIAS_CATEGORY_HINTS)


def event_type_for(event: PromptEvent) -> SentinelEventType:
    """Map the telemetry action vocabulary onto the Sentinel event enum.

    Raises ``UnmappableEvent`` for ordinary activity — a prompt that was simply
    allowed is not one of the five Prompt Shields events the SOC subscribed to,
    and forwarding it would bury the signal (and bill the customer per GB) for
    nothing.

    ``Anonymised`` is intentionally unreachable here: ``prompt_events`` has no
    signal distinguishing anonymisation (placeholder substitution) from
    redaction, so emitting it would be a guess. The value stays in the wire
    contract for clients that later report the distinction.
    """
    action = event.action
    if action == PromptEventAction.REDACTED:
        return SentinelEventType.redacted
    if action == PromptEventAction.BLOCKED:
        return SentinelEventType.blocked
    if action == PromptEventAction.FLAGGED:
        if _has_bias_signal(event.pii_categories or {}):
            return SentinelEventType.bias_flagged
        return SentinelEventType.coached
    # `allowed` / `logged` / no action: only a violation is worth forwarding,
    # as the coaching moment the user was shown.
    if event.event_kind == PromptEventKind.VIOLATION:
        return SentinelEventType.coached
    raise UnmappableEvent(
        f"activity event with action={action.value if action else 'none'} "
        "has no Sentinel event type"
    )


def sensitive_type_for(event: PromptEvent) -> str | None:
    """Join the event's PII categories into the doc's ``SSN+EIN`` shape.

    Returns None (never an empty string) when the event carried no categories —
    data-schema.md marks the column nullable and forbids empty strings.
    """
    categories = event.pii_categories or {}
    if not categories:
        return None
    names: list[str] = []
    for key in sorted(categories):
        pretty = _SENSITIVE_TYPE_NAMES.get(key.strip().lower())
        if pretty is None:
            pretty = key.strip().replace("_", " ").title()
        if pretty and pretty not in names:
            names.append(pretty)
    return "+".join(names) or None


def severity_for(event: PromptEvent, event_type: SentinelEventType) -> SentinelSeverity:
    if event.severity is not None:
        return _SEVERITY_MAP[event.severity]
    return _DEFAULT_SEVERITY[event_type]


def redaction_token_count_for(event: PromptEvent, event_type: SentinelEventType) -> int | None:
    """Total redacted spans — only meaningful for Redacted / Anonymised."""
    if event_type not in (SentinelEventType.redacted, SentinelEventType.anonymised):
        return None
    categories = event.pii_categories or {}
    total = sum(v for v in categories.values() if isinstance(v, int) and not isinstance(v, bool))
    return total or None


def detail_for(
    event: PromptEvent,
    event_type: SentinelEventType,
    tool: str,
    sensitive_type: str | None,
) -> str:
    """A short structured description — never prompt content.

    Assembled purely from the enum values and category names already on the
    row, so there is no path by which user text reaches this string.
    """
    verb = {
        SentinelEventType.redacted: "Redacted",
        SentinelEventType.anonymised: "Anonymised",
        SentinelEventType.blocked: "Blocked",
        SentinelEventType.coached: "Coached on",
        SentinelEventType.bias_flagged: "Flagged",
    }[event_type]
    subject = sensitive_type if sensitive_type else "a policy-relevant prompt"
    detail = f"{verb} {subject} in {tool}"
    if event.occurrences > 1:
        detail += f" ({event.occurrences} occurrences)"
    return detail


def user_for(event: PromptEvent) -> str:
    """UPN when the client attributed the event, else a stable stand-in.

    ``User`` is a required column, so an unattributed event still needs a
    value. A device-scoped label keeps the row honest — the SOC can see the
    endpoint even when identity is missing — rather than inventing a person.
    """
    if event.user_external_id and event.user_external_id.strip():
        return event.user_external_id.strip()
    if event.device_fingerprint and event.device_fingerprint.strip():
        return f"device:{event.device_fingerprint.strip()}"
    return "unattributed"


def event_id_for(event: PromptEvent) -> str:
    """Stable unique id used for idempotent send and dedup verification.

    The telemetry row's own UUID — not a counter — so a replayed dead letter
    carries the same ``EventId`` as the original attempt.
    """
    return f"EV-{event.id}"


# ─── Row assembly ────────────────────────────────────────────────────


def build_row(
    event: PromptEvent,
    *,
    tenant_id: Any,
    context: MappingContext,
) -> dict[str, Any]:
    """Map one ``PromptEvent`` onto a ``PromptShieldsActivity_CL`` row.

    Raises ``UnmappableEvent`` when the event cannot be represented: ordinary
    activity (no Sentinel event type) or a missing ``prompt_hash``. The hash is
    a required column and the customer correlates on it, so a synthesised value
    would be worse than skipping the row.
    """
    event_type = event_type_for(event)

    if not event.prompt_hash:
        raise UnmappableEvent("missing prompt_hash (required column)")

    tool = display_tool_name(event)
    sensitive_type = sensitive_type_for(event)

    user = user_for(event)
    directory = context.directory_by_user.get(user.lower(), DirectoryMatch())
    device = context.devices_by_fingerprint.get(event.device_fingerprint or "", DeviceMatch())

    return {
        "TimeGenerated": event.occurred_at,
        "EventId": event_id_for(event),
        "TenantId": str(tenant_id),
        "User": user,
        "UserAadObjectId": directory.aad_object_id,
        "Department": directory.department,
        "AiTool": tool,
        "IsShadowAi": is_shadow_ai(event, context.sanctioned_tools),
        "EventType": event_type.value,
        "SensitiveType": sensitive_type,
        "Severity": severity_for(event, event_type).value,
        "Detail": detail_for(event, event_type, tool, sensitive_type),
        # `prompt_events` carries no policy linkage; PolicyViolation is a
        # separate path. Nullable columns, so they stay null until a future
        # slice joins the two.
        "PolicyId": None,
        "PolicyName": None,
        "EndpointId": device.endpoint_id,
        "EndpointPlatform": device.platform,
        "RedactionTokenCount": redaction_token_count_for(event, event_type),
        "PromptHash": event.prompt_hash,
    }
