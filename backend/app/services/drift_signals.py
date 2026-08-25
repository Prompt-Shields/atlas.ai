"""Automated drift detection — turn the 90-day re-attestation cycle
into an event-triggered one when a real change has happened.

Closes the operational gap on Øystein priority #3 ("Holde
informasjonen oppdatert"). Today re-attestation is purely time-based:
every 90 days a Review gets enqueued, the owner gets DM'd, they
confirm. That misses the case where *something already changed
yesterday* — model deprecation, tool rebrand, or a registration
that's internally inconsistent (e.g. customer_pii data class but
LOW risk tier).

This module is the pure-function rule engine. The DB-touching
dispatcher lives in `drift_signal_dispatcher.py`; tests on the
rules are in `tests/unit/test_drift_signals.py`.

Three categories of signal in v0.1:

  MODEL_DEPRECATION  — UseCase.tool matches a known deprecated model.
                       Curated list below; admin-editable in v0.2
                       (new TenantDriftSignalOverride table).
  TOOL_REBRAND       — UseCase.tool matches a legacy product name
                       that's since been renamed (e.g. "Bing Chat"
                       → Microsoft Copilot).
  RISK_TIER_MISMATCH — UseCase has sensitive data classes but
                       risk_tier=LOW. Self-consistency check.

Each finding has a stable `signature` string used for dedup so the
dispatcher doesn't keep re-flagging the same drift on every tick.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.use_case import UseCase, UseCaseRiskTier

# Severity ordering: HIGH > MEDIUM > LOW. The dispatcher uses this to
# pick the most severe finding when summarising a use case with
# multiple flags. Plain strings (not enum) so they round-trip cleanly
# through review notes and audit logs.
SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"


@dataclass(frozen=True)
class DriftFinding:
    """One drift signal applied to one UseCase.

    `signature` is the stable identity for dedup — same input → same
    signature, so the dispatcher can recognise "we've already flagged
    this exact drift on this use case" without re-running the rules
    against a remembered set.
    """

    kind: str
    signature: str
    severity: str
    title: str
    detail: str


# ─── Curated signals (v0.1 — admin-editable in v0.2) ─────────────────


# Deprecated model names. Match is case-insensitive substring against
# UseCase.tool. The "as of" dates inform the detail copy so the IT
# lead can see the real-world context, not a magic "drift detected".
DEPRECATED_MODELS: tuple[dict[str, str], ...] = (
    {
        "match": "gpt-3.5",
        "label": "GPT-3.5 / gpt-3.5-turbo",
        "deprecated_as_of": "2025-01",
        "successor": "GPT-4o (or GPT-4o-mini for low-cost)",
    },
    {
        "match": "codex",
        "label": "OpenAI Codex / code-davinci",
        "deprecated_as_of": "2023-03",
        "successor": "GPT-4o or a current code-tuned model",
    },
    {
        "match": "palm 2",
        "label": "Google PaLM 2",
        "deprecated_as_of": "2024-10",
        "successor": "Gemini 1.5 / 2.0 family",
    },
    {
        "match": "claude 1",
        "label": "Claude 1 / Claude Instant",
        "deprecated_as_of": "2024-07",
        "successor": "Claude 3.5 / 4.x family",
    },
    {
        "match": "claude instant",
        "label": "Claude Instant",
        "deprecated_as_of": "2024-07",
        "successor": "Claude 3.5 Haiku",
    },
    {
        "match": "llama 2",
        "label": "Meta Llama 2",
        "deprecated_as_of": "2024-04",
        "successor": "Llama 3 / Llama 3.1+",
    },
    {
        "match": "gemini 1.0",
        "label": "Gemini 1.0 Pro",
        "deprecated_as_of": "2024-12",
        "successor": "Gemini 1.5 Pro / 2.0",
    },
)


# Legacy product names that have been renamed. Surfacing these helps
# the IT lead reconcile the inventory with what users *actually* see
# in their daily tools.
TOOL_REBRANDS: tuple[dict[str, str], ...] = (
    {
        "match": "bing chat",
        "current_name": "Microsoft Copilot",
        "renamed_as_of": "2023-11",
    },
    {
        "match": "bard",
        "current_name": "Gemini",
        "renamed_as_of": "2024-02",
    },
    {
        "match": "chatgpt enterprise",
        # Still a real product name, but the inventory often confuses
        # it with the free tier. Surface to confirm.
        "current_name": "ChatGPT Enterprise (confirm: still the org's contracted tier?)",
        "renamed_as_of": None,
    },
)


# Data classes that signal a use case shouldn't be LOW risk tier.
# Curated to match the existing taxonomy in the survey-bot and the
# registry form (see app/services/survey_flow.DATA_CLASS_OPTIONS).
SENSITIVE_DATA_CLASSES: frozenset[str] = frozenset(
    {
        "customer_pii",
        "employee_pii",
        "donor_pii",
        "vendor_pii",
        "phi",
        "financial_data",
        "strategy_docs",
    }
)


# ─── Rule engine ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TenantSignalSpec:
    """A tenant-defined drift signal, denormalised for the pure rule engine.

    The dispatcher loads TenantDriftSignal rows from the DB and
    converts them to this shape before calling detect_drift, so the
    rule engine itself stays pure (no DB types).

    `kind` must be one of {"MODEL_DEPRECATION", "TOOL_REBRAND"}.
    """

    kind: str
    match: str  # case-insensitive substring against UseCase.tool
    label: str
    severity: str
    # For MODEL_DEPRECATION: successor model.
    # For TOOL_REBRAND: current product name.
    successor_or_current_name: str | None = None
    effective_as_of: str | None = None


def detect_drift(
    use_case: UseCase,
    *,
    tenant_signals: list[TenantSignalSpec] | None = None,
) -> list[DriftFinding]:
    """Apply all rules to a use case. Returns 0+ findings.

    Pure function. No DB, no I/O. Same input → same output.

    `tenant_signals` lets callers merge per-tenant overrides on top
    of the curated globals. The signature for tenant signals is
    prefixed with `tenant:` so they don't collide with global
    signatures and the dispatcher's signature_in_notes dedup keeps
    working across global + tenant findings on the same use case.
    """
    findings: list[DriftFinding] = []
    findings.extend(_check_deprecated_models(use_case))
    findings.extend(_check_tool_rebrands(use_case))
    findings.extend(_check_risk_tier_mismatch(use_case))
    if tenant_signals:
        findings.extend(_check_tenant_signals(use_case, tenant_signals))
    return findings


def _check_tenant_signals(uc: UseCase, signals: list[TenantSignalSpec]) -> list[DriftFinding]:
    tool_lower = (uc.tool or "").lower()
    out: list[DriftFinding] = []
    for s in signals:
        match_lower = (s.match or "").lower().strip()
        if not match_lower:
            continue
        if match_lower not in tool_lower:
            continue

        if s.kind == "TOOL_REBRAND":
            # Same exemption as the global rebrand rule — if the
            # current name is already in the tool field, the team
            # has updated; don't double-flag.
            current = (s.successor_or_current_name or "").lower()
            if current and current in tool_lower:
                continue

        if s.kind == "MODEL_DEPRECATION":
            successor = s.successor_or_current_name or "a current alternative"
            since = s.effective_as_of or "an earlier release"
            detail = (
                f"This use case is registered as using *{uc.tool}*. "
                f"Per tenant policy, *{s.label}* is deprecated as of "
                f"{since}. Confirm migration to {successor}."
            )
            title = f"{s.label} is deprecated (tenant policy)"
        elif s.kind == "TOOL_REBRAND":
            current = s.successor_or_current_name or "the current product name"
            since = s.effective_as_of or "an earlier release"
            detail = (
                f"This use case names *{uc.tool}*. Per tenant policy, "
                f"the current product name is *{current}* as of "
                f"{since}. Update the tool field so the inventory "
                f"matches what users see."
            )
            title = f"{s.label} was renamed (tenant policy)"
        else:
            # Unknown kind — defensive skip (the enum constrains this
            # at the DB layer, but better to no-op than crash).
            continue

        out.append(
            DriftFinding(
                kind=s.kind,
                signature=f"tenant:{s.kind}::{match_lower}",
                severity=s.severity,
                title=title,
                detail=detail,
            )
        )
    return out


def _check_deprecated_models(uc: UseCase) -> list[DriftFinding]:
    tool_lower = (uc.tool or "").lower()
    out: list[DriftFinding] = []
    for spec in DEPRECATED_MODELS:
        if spec["match"] in tool_lower:
            out.append(
                DriftFinding(
                    kind="MODEL_DEPRECATION",
                    signature=f"MODEL_DEPRECATION::{spec['match']}",
                    severity=SEVERITY_HIGH,
                    title=f"{spec['label']} is deprecated",
                    detail=(
                        f"This use case is registered as using "
                        f"*{uc.tool}*, which the vendor deprecated as of "
                        f"{spec['deprecated_as_of']}. Confirm whether the "
                        f"team has migrated to {spec['successor']} or "
                        f"another current model, and update the tool "
                        f"field accordingly."
                    ),
                )
            )
    return out


def _check_tool_rebrands(uc: UseCase) -> list[DriftFinding]:
    tool_lower = (uc.tool or "").lower()
    out: list[DriftFinding] = []
    for spec in TOOL_REBRANDS:
        if spec["match"] in tool_lower:
            # The rebrand signal is only useful if the *current* name
            # isn't already in the tool field — otherwise the team has
            # already updated.
            current_lower = (spec["current_name"] or "").lower()
            if current_lower and current_lower in tool_lower:
                continue
            since = spec.get("renamed_as_of") or "an earlier release"
            out.append(
                DriftFinding(
                    kind="TOOL_REBRAND",
                    signature=f"TOOL_REBRAND::{spec['match']}",
                    severity=SEVERITY_MEDIUM,
                    title=f"{uc.tool} was renamed",
                    detail=(
                        f"This use case names *{uc.tool}*, which the "
                        f"vendor rebranded to *{spec['current_name']}* as "
                        f"of {since}. Update the tool field so the "
                        f"inventory matches what users see day-to-day."
                    ),
                )
            )
    return out


def _check_risk_tier_mismatch(uc: UseCase) -> list[DriftFinding]:
    """LOW risk_tier with sensitive data classes = misclassification."""
    if uc.risk_tier != UseCaseRiskTier.LOW:
        return []

    # data_classes is JSON-as-Text — we don't parse it here (rule
    # engine stays pure, no JSON imports). The dispatcher passes us a
    # detached UseCase with the raw string, but for testability we
    # accept either a list or the raw string and treat empty as no
    # data classes.
    raw = getattr(uc, "data_classes", None)
    parsed_classes = _parse_data_classes(raw)
    if not parsed_classes:
        return []

    intersect = SENSITIVE_DATA_CLASSES & {c.lower() for c in parsed_classes}
    if not intersect:
        return []

    flagged = sorted(intersect)
    return [
        DriftFinding(
            kind="RISK_TIER_MISMATCH",
            # Signature is class-level (not list-of-classes-level) so
            # adding another sensitive class doesn't fork a new
            # finding when an old one is already open.
            signature="RISK_TIER_MISMATCH::low_with_sensitive_data",
            severity=SEVERITY_HIGH,
            title="LOW risk tier with sensitive data classes",
            detail=(
                f"This use case is classified *LOW* risk but includes "
                f"sensitive data class(es): {', '.join(flagged)}. "
                f"LOW is typically reserved for non-PII general "
                f"productivity. Re-rate to MEDIUM or HIGH, or remove "
                f"the data class if it no longer applies."
            ),
        )
    ]


# ─── Helpers ─────────────────────────────────────────────────────────


def _parse_data_classes(raw: object) -> list[str]:
    """Accept either a list (test path) or a JSON-encoded string (DB path)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(c) for c in raw if c]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            import json

            parsed = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(parsed, list):
            return [str(c) for c in parsed if c]
    return []


# ─── Notes-encoding helpers used by the dispatcher ───────────────────


# When we write findings into a UseCaseReview.notes field, we prefix
# each finding with this marker so the dispatcher can:
#   (a) dedup — skip a finding if its signature already appears
#   (b) parse — strip drift-section out if the reviewer adds free-form
#       notes underneath when marking reviewed
DRIFT_MARKER_PREFIX = "[atlas-drift]"


def encode_findings_for_notes(findings: list[DriftFinding]) -> str:
    """Format a list of findings as a multi-paragraph notes block.

    Schema:
      [atlas-drift] kind=K signature=S severity=SEV
      <title>
      <detail>
      ─

    The dispatcher uses signature_in_notes() to dedup.
    """
    blocks: list[str] = []
    for f in findings:
        blocks.append(
            f"{DRIFT_MARKER_PREFIX} kind={f.kind} "
            f"signature={f.signature} severity={f.severity}\n"
            f"{f.title}\n"
            f"{f.detail}\n─"
        )
    return "\n\n".join(blocks)


def signature_in_notes(notes: str | None, signature: str) -> bool:
    """Does this notes blob already mention this drift signature?"""
    if not notes:
        return False
    return f"signature={signature}" in notes
