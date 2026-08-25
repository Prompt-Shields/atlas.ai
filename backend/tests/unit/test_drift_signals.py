"""Unit tests for app.services.drift_signals.

Pure-function tests on the rule engine + notes-encoding helpers.
The DB-touching dispatcher is integration territory (creates Review
rows, needs a UseCase fixture); this file owns the rule logic.

Coverage priorities:
  1. Each rule fires on the right input and ONLY on the right input.
  2. Notes encoding round-trips: encode_findings_for_notes →
     signature_in_notes returns True for every encoded signature.
  3. Edge cases: empty data_classes, mixed case, the "current name
     already in the tool field" exemption for rebrands.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest

from app.models.use_case import (
    UseCase,
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)
from app.services.drift_signals import (
    DRIFT_MARKER_PREFIX,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    DriftFinding,
    detect_drift,
    encode_findings_for_notes,
    signature_in_notes,
)

pytestmark = [pytest.mark.unit]


# ─── Helpers ─────────────────────────────────────────────────────────


def _make_use_case(
    *,
    tool: str = "Microsoft Copilot",
    risk_tier: UseCaseRiskTier = UseCaseRiskTier.MEDIUM,
    data_classes: list[str] | str | None = None,
) -> UseCase:
    """Build an unsaved UseCase instance for rule-engine testing.

    We don't go through the DB — these are pure-function tests, so we
    instantiate the SQLAlchemy model directly. Required fields get
    defaults that make the row "valid enough" for the rule engine to
    inspect without raising.
    """
    uc = UseCase(
        title="Test use case",
        tool=tool,
        department="Engineering",
        status=UseCaseStatus.ACTIVE,
        risk_tier=risk_tier,
        source=UseCaseSource.FORM,
    )
    # SQLAlchemy ORM defaults aren't applied until commit, so we set
    # the runtime attributes the rule engine inspects.
    uc.id = uuid.uuid4()
    uc.tenant_id = uuid.uuid4()
    if isinstance(data_classes, list):
        # Match how the dispatcher passes it through — JSON-encoded string
        import json

        uc.data_classes = json.dumps(data_classes)
    elif isinstance(data_classes, str):
        uc.data_classes = data_classes
    else:
        uc.data_classes = "[]"
    return uc


# ─── MODEL_DEPRECATION rule ──────────────────────────────────────────


class TestModelDeprecationRule:
    def test_gpt_35_flagged(self) -> None:
        uc = _make_use_case(tool="GPT-3.5-turbo for grant drafting")
        findings = detect_drift(uc)
        kinds = [f.kind for f in findings]
        assert "MODEL_DEPRECATION" in kinds
        dep = next(f for f in findings if f.kind == "MODEL_DEPRECATION")
        assert dep.severity == SEVERITY_HIGH
        assert "deprecated" in dep.title.lower()
        assert "GPT-3.5" in dep.title

    def test_palm_2_flagged(self) -> None:
        uc = _make_use_case(tool="Google PaLM 2 summarisation")
        findings = detect_drift(uc)
        assert any(f.kind == "MODEL_DEPRECATION" for f in findings)

    def test_case_insensitive(self) -> None:
        uc = _make_use_case(tool="GPT-3.5")
        assert any(f.kind == "MODEL_DEPRECATION" for f in detect_drift(uc))

    def test_current_model_not_flagged(self) -> None:
        for tool in ("GPT-4o", "Claude 3.5 Sonnet", "Gemini 2.0", "Microsoft Copilot"):
            uc = _make_use_case(tool=tool)
            kinds = [f.kind for f in detect_drift(uc)]
            assert "MODEL_DEPRECATION" not in kinds, f"{tool} false-positive"

    def test_stable_signature(self) -> None:
        """Same input → same signature, so the dispatcher's dedup works."""
        uc = _make_use_case(tool="GPT-3.5")
        sigs1 = [f.signature for f in detect_drift(uc)]
        sigs2 = [f.signature for f in detect_drift(uc)]
        assert sigs1 == sigs2


# ─── TOOL_REBRAND rule ───────────────────────────────────────────────


class TestToolRebrandRule:
    def test_bing_chat_flagged(self) -> None:
        uc = _make_use_case(tool="Bing Chat for research")
        findings = detect_drift(uc)
        assert any(f.kind == "TOOL_REBRAND" for f in findings)
        rb = next(f for f in findings if f.kind == "TOOL_REBRAND")
        assert rb.severity == SEVERITY_MEDIUM
        assert "Copilot" in rb.detail  # mentions current name

    def test_bard_flagged(self) -> None:
        uc = _make_use_case(tool="Google Bard")
        findings = detect_drift(uc)
        assert any(f.kind == "TOOL_REBRAND" for f in findings)

    def test_current_name_already_present_skips_rebrand(self) -> None:
        """If the tool string already includes the new name, the user has
        already updated — don't double-flag."""
        # "Microsoft Copilot (formerly Bing Chat)" should NOT trigger
        # because current_name ("microsoft copilot") is in the tool.
        uc = _make_use_case(tool="Microsoft Copilot (formerly Bing Chat)")
        findings = detect_drift(uc)
        assert not any(f.kind == "TOOL_REBRAND" for f in findings)

    def test_unrelated_tool_not_flagged(self) -> None:
        uc = _make_use_case(tool="Claude 3.5 Sonnet")
        assert not any(f.kind == "TOOL_REBRAND" for f in detect_drift(uc))


# ─── RISK_TIER_MISMATCH rule ─────────────────────────────────────────


class TestRiskTierMismatchRule:
    def test_low_with_customer_pii_flagged(self) -> None:
        uc = _make_use_case(
            tool="Microsoft Copilot",  # avoid other rules
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=["customer_pii"],
        )
        findings = detect_drift(uc)
        assert any(f.kind == "RISK_TIER_MISMATCH" for f in findings)
        mm = next(f for f in findings if f.kind == "RISK_TIER_MISMATCH")
        assert mm.severity == SEVERITY_HIGH
        assert "customer_pii" in mm.detail

    def test_low_with_financial_data_flagged(self) -> None:
        uc = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=["financial_data"],
        )
        assert any(f.kind == "RISK_TIER_MISMATCH" for f in detect_drift(uc))

    def test_medium_with_pii_not_flagged(self) -> None:
        uc = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.MEDIUM,
            data_classes=["customer_pii"],
        )
        kinds = [f.kind for f in detect_drift(uc)]
        assert "RISK_TIER_MISMATCH" not in kinds

    def test_low_with_no_data_classes_not_flagged(self) -> None:
        uc = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=[],
        )
        kinds = [f.kind for f in detect_drift(uc)]
        assert "RISK_TIER_MISMATCH" not in kinds

    def test_low_with_only_non_sensitive_classes_not_flagged(self) -> None:
        uc = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=["public_data"],  # not in SENSITIVE_DATA_CLASSES
        )
        kinds = [f.kind for f in detect_drift(uc)]
        assert "RISK_TIER_MISMATCH" not in kinds

    def test_handles_malformed_data_classes(self) -> None:
        """A malformed JSON blob in data_classes shouldn't crash the engine."""
        uc = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.LOW,
            data_classes="{not json",
        )
        # Should not raise; should just produce no mismatch finding
        findings = detect_drift(uc)
        assert not any(f.kind == "RISK_TIER_MISMATCH" for f in findings)

    def test_signature_stable_regardless_of_class_ordering(self) -> None:
        """Two different sensitive-class sets share the same signature so a
        new class doesn't fork a duplicate finding when one is already open."""
        a = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=["customer_pii", "donor_pii"],
        )
        b = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=["customer_pii", "phi", "donor_pii"],
        )
        sig_a = next(f.signature for f in detect_drift(a) if f.kind == "RISK_TIER_MISMATCH")
        sig_b = next(f.signature for f in detect_drift(b) if f.kind == "RISK_TIER_MISMATCH")
        assert sig_a == sig_b


# ─── Combined / no-finding behaviour ─────────────────────────────────


class TestCombined:
    def test_no_findings_for_well_formed_use_case(self) -> None:
        uc = _make_use_case(
            tool="Microsoft Copilot",
            risk_tier=UseCaseRiskTier.MEDIUM,
            data_classes=["customer_pii"],
        )
        assert detect_drift(uc) == []

    def test_multiple_findings_compound(self) -> None:
        """A use case can trip multiple rules at once — all are returned."""
        uc = _make_use_case(
            tool="GPT-3.5 via Bing Chat",  # both deprecation + rebrand
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=["customer_pii"],  # also risk mismatch
        )
        findings = detect_drift(uc)
        kinds = {f.kind for f in findings}
        assert kinds == {
            "MODEL_DEPRECATION",
            "TOOL_REBRAND",
            "RISK_TIER_MISMATCH",
        }


# ─── Notes encoding / dedup ──────────────────────────────────────────


class TestNotesEncoding:
    def test_encode_includes_marker_for_each_finding(self) -> None:
        findings = [
            DriftFinding(
                kind="MODEL_DEPRECATION",
                signature="MODEL_DEPRECATION::gpt-3.5",
                severity=SEVERITY_HIGH,
                title="GPT-3.5 is deprecated",
                detail="Detail copy here.",
            ),
            DriftFinding(
                kind="RISK_TIER_MISMATCH",
                signature="RISK_TIER_MISMATCH::low_with_sensitive_data",
                severity=SEVERITY_HIGH,
                title="LOW tier with sensitive data",
                detail="More detail.",
            ),
        ]
        encoded = encode_findings_for_notes(findings)
        # One marker per finding
        assert encoded.count(DRIFT_MARKER_PREFIX) == 2
        # Each signature must appear in the encoded blob
        for f in findings:
            assert f.signature in encoded
            assert f.title in encoded
            assert f.detail in encoded

    def test_signature_in_notes_finds_real_signatures(self) -> None:
        findings = [
            DriftFinding(
                kind="MODEL_DEPRECATION",
                signature="MODEL_DEPRECATION::gpt-3.5",
                severity=SEVERITY_HIGH,
                title="t",
                detail="d",
            ),
        ]
        encoded = encode_findings_for_notes(findings)
        assert signature_in_notes(encoded, "MODEL_DEPRECATION::gpt-3.5")
        # Unrelated signature is not falsely matched
        assert not signature_in_notes(encoded, "MODEL_DEPRECATION::palm 2")

    def test_signature_in_notes_handles_none(self) -> None:
        assert not signature_in_notes(None, "anything")
        assert not signature_in_notes("", "anything")

    def test_round_trip_via_detect_drift(self) -> None:
        """End-to-end: rule engine → encode → dedup-check finds every signature."""
        uc = _make_use_case(
            tool="GPT-3.5 with Bing Chat",
            risk_tier=UseCaseRiskTier.LOW,
            data_classes=["customer_pii"],
        )
        findings = detect_drift(uc)
        assert findings  # sanity
        encoded = encode_findings_for_notes(findings)
        for f in findings:
            assert signature_in_notes(encoded, f.signature)


# ─── Frozen-dataclass invariant ──────────────────────────────────────


class TestDataclassShape:
    def test_finding_is_frozen(self) -> None:
        f = DriftFinding(kind="X", signature="x::1", severity="LOW", title="t", detail="d")
        with pytest.raises(dataclasses.FrozenInstanceError):
            f.title = "mutated"  # type: ignore[misc]
