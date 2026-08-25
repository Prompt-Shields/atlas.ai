"""Unit tests for the tenant-signal merge path in app.services.drift_signals.

Coverage:
  1. Tenant signals fire when match_pattern is in the tool field.
  2. Tenant signal `signature` is prefixed with `tenant:` so it never
     collides with global signatures (round-trip + dedup safety).
  3. The rebrand exemption applies to tenant rebrands too — if the
     current name is already in tool, no finding.
  4. Combined globals + tenant signals on the same use case.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.use_case import (
    UseCase,
    UseCaseRiskTier,
    UseCaseSource,
    UseCaseStatus,
)
from app.services.drift_signals import (
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    TenantSignalSpec,
    detect_drift,
)

pytestmark = [pytest.mark.unit]


def _make_use_case(*, tool: str) -> UseCase:
    uc = UseCase(
        title="t",
        tool=tool,
        department="d",
        status=UseCaseStatus.ACTIVE,
        risk_tier=UseCaseRiskTier.MEDIUM,
        source=UseCaseSource.FORM,
    )
    uc.id = uuid.uuid4()
    uc.tenant_id = uuid.uuid4()
    uc.data_classes = "[]"
    return uc


class TestTenantSignalsFire:
    def test_tenant_model_deprecation(self) -> None:
        uc = _make_use_case(tool="Notion AI for meeting notes")
        signals = [
            TenantSignalSpec(
                kind="MODEL_DEPRECATION",
                match="notion ai",
                label="Notion AI",
                severity=SEVERITY_HIGH,
                successor_or_current_name="Microsoft Copilot",
                effective_as_of="2026-Q1",
            )
        ]
        findings = detect_drift(uc, tenant_signals=signals)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "MODEL_DEPRECATION"
        assert f.severity == SEVERITY_HIGH
        assert "Notion AI" in f.title
        assert "Microsoft Copilot" in f.detail
        assert "2026-Q1" in f.detail

    def test_tenant_tool_rebrand(self) -> None:
        uc = _make_use_case(tool="AskGPT internal assistant")
        signals = [
            TenantSignalSpec(
                kind="TOOL_REBRAND",
                match="askgpt",
                label="AskGPT",
                severity=SEVERITY_MEDIUM,
                successor_or_current_name="AcmeAssist",
                effective_as_of="2026-03",
            )
        ]
        findings = detect_drift(uc, tenant_signals=signals)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "TOOL_REBRAND"
        assert "AcmeAssist" in f.detail

    def test_rebrand_exemption_when_current_name_present(self) -> None:
        uc = _make_use_case(tool="AcmeAssist (formerly AskGPT)")
        signals = [
            TenantSignalSpec(
                kind="TOOL_REBRAND",
                match="askgpt",
                label="AskGPT",
                severity=SEVERITY_MEDIUM,
                successor_or_current_name="AcmeAssist",
            )
        ]
        # The team already renamed — don't flag
        findings = detect_drift(uc, tenant_signals=signals)
        assert findings == []

    def test_tenant_signature_isolated_from_globals(self) -> None:
        """Tenant signatures must be prefixed `tenant:` so they never
        collide with global ones, even if the match pattern matches a
        curated global."""
        uc = _make_use_case(tool="Claude 1 still in use")
        signals = [
            TenantSignalSpec(
                kind="MODEL_DEPRECATION",
                match="claude 1",  # also matches a curated global
                label="Claude 1 (per tenant policy)",
                severity=SEVERITY_HIGH,
            )
        ]
        findings = detect_drift(uc, tenant_signals=signals)
        sigs = [f.signature for f in findings]
        global_sigs = [s for s in sigs if not s.startswith("tenant:")]
        tenant_sigs = [s for s in sigs if s.startswith("tenant:")]
        # Should have one of each — global + tenant. Not collapsed.
        assert len(global_sigs) == 1
        assert len(tenant_sigs) == 1
        # Tenant signature uses the lowercased match
        assert tenant_sigs[0] == "tenant:MODEL_DEPRECATION::claude 1"

    def test_no_match_no_finding(self) -> None:
        uc = _make_use_case(tool="Microsoft Copilot")
        signals = [
            TenantSignalSpec(
                kind="MODEL_DEPRECATION",
                match="notion ai",
                label="Notion AI",
                severity=SEVERITY_HIGH,
            )
        ]
        assert detect_drift(uc, tenant_signals=signals) == []

    def test_case_insensitive_match(self) -> None:
        uc = _make_use_case(tool="NOTION AI for legal docs")
        signals = [
            TenantSignalSpec(
                kind="MODEL_DEPRECATION",
                match="Notion AI",
                label="Notion AI",
                severity=SEVERITY_HIGH,
            )
        ]
        findings = detect_drift(uc, tenant_signals=signals)
        assert len(findings) == 1

    def test_empty_match_pattern_skipped(self) -> None:
        uc = _make_use_case(tool="anything")
        signals = [
            TenantSignalSpec(
                kind="MODEL_DEPRECATION",
                match="   ",  # whitespace-only
                label="x",
                severity=SEVERITY_HIGH,
            )
        ]
        assert detect_drift(uc, tenant_signals=signals) == []

    def test_unknown_kind_defensive_skip(self) -> None:
        uc = _make_use_case(tool="copilot")
        signals = [
            TenantSignalSpec(
                kind="MADE_UP_KIND",
                match="copilot",
                label="x",
                severity=SEVERITY_HIGH,
            )
        ]
        # Should not raise; should produce no finding for the unknown kind
        findings = [f for f in detect_drift(uc, tenant_signals=signals) if f.kind == "MADE_UP_KIND"]
        assert findings == []


class TestCombinedGlobalAndTenant:
    def test_global_plus_tenant_findings_both_appear(self) -> None:
        # Tool field triggers BOTH a curated global rule AND a tenant rule
        uc = _make_use_case(tool="GPT-3.5 via internal AskGPT wrapper")
        signals = [
            TenantSignalSpec(
                kind="TOOL_REBRAND",
                match="askgpt",
                label="AskGPT",
                severity=SEVERITY_MEDIUM,
                successor_or_current_name="AcmeAssist",
            )
        ]
        findings = detect_drift(uc, tenant_signals=signals)
        kinds = [f.kind for f in findings]
        # Global GPT-3.5 deprecation + tenant AskGPT rebrand both fire
        assert "MODEL_DEPRECATION" in kinds  # from curated globals
        assert "TOOL_REBRAND" in kinds  # from tenant signal
