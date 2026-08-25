"""Unit tests for app.services.integration_registry.

Pure-function tests over the static provider catalogue.
"""

from __future__ import annotations

import pytest

from app.models.integration import IntegrationProvider
from app.services.integration_registry import (
    get_provider,
    list_providers,
    onboarding_recommended,
)

pytestmark = [pytest.mark.unit]


class TestRegistry:
    def test_every_provider_enum_has_meta(self) -> None:
        meta_providers = {m.provider for m in list_providers()}
        for p in IntegrationProvider:
            assert p in meta_providers, f"registry missing {p.value}"

    def test_microsoft_providers_use_microsoft_endpoints(self) -> None:
        for p in (
            IntegrationProvider.MICROSOFT_ENTRA_ID,
            IntegrationProvider.MICROSOFT_INTUNE,
            IntegrationProvider.MICROSOFT_PURVIEW,
            IntegrationProvider.MICROSOFT_DEFENDER_CASB,
        ):
            meta = get_provider(p)
            assert meta.vendor == "microsoft"
            assert meta.available is True
            assert meta.authorize_url is not None
            assert "login.microsoftonline.com" in meta.authorize_url

    def test_slack_provider(self) -> None:
        meta = get_provider(IntegrationProvider.SLACK)
        assert meta.vendor == "slack"
        assert meta.available is True
        assert meta.onboarding_recommended is True

    def test_aws_and_gcp_placeholders(self) -> None:
        for p in (
            IntegrationProvider.AWS,
            IntegrationProvider.AWS_IAM_IDENTITY_CENTER,
            IntegrationProvider.AWS_BEDROCK,
            IntegrationProvider.GCP,
            IntegrationProvider.GCP_WORKSPACE,
            IntegrationProvider.GCP_VERTEX,
        ):
            meta = get_provider(p)
            assert meta.available is False
            assert meta.onboarding_recommended is False
            assert meta.authorize_url is None

    def test_available_only_filter(self) -> None:
        available = list_providers(available_only=True)
        # Microsoft 4 + Slack 1 + Jamf Pro + Kandji + JumpCloud = 8
        # + 5 cost connectors (Anthropic, OpenAI, Cursor, GitHub Copilot, Vercel) = 13
        # + Sentinel = 14.
        # AWS / GCP excluded (Coming Soon).
        assert len(available) == 14
        for m in available:
            assert m.available is True

    def test_jamf_pro_is_available(self) -> None:
        meta = get_provider(IntegrationProvider.JAMF_PRO)
        assert meta.available is True
        assert meta.category == "device"
        assert meta.vendor == "other"
        # Jamf uses Basic-auth-for-bearer rather than OAuth.
        assert meta.authorize_url is None

    def test_kandji_is_available(self) -> None:
        meta = get_provider(IntegrationProvider.KANDJI)
        assert meta.available is True
        assert meta.category == "device"
        assert meta.vendor == "other"
        assert meta.authorize_url is None

    def test_jumpcloud_is_available(self) -> None:
        meta = get_provider(IntegrationProvider.JUMPCLOUD)
        assert meta.available is True
        assert meta.category == "device"
        assert meta.vendor == "other"
        # JumpCloud uses x-api-key header, not OAuth.
        assert meta.authorize_url is None

    def test_onboarding_recommended(self) -> None:
        rec = onboarding_recommended()
        # Entra ID + Slack.
        providers = {r.provider for r in rec}
        assert IntegrationProvider.MICROSOFT_ENTRA_ID in providers
        assert IntegrationProvider.SLACK in providers
        assert len(rec) == 2

    def test_entra_scopes_include_directory_read(self) -> None:
        meta = get_provider(IntegrationProvider.MICROSOFT_ENTRA_ID)
        assert "User.Read.All" in meta.scopes
        assert "Directory.Read.All" in meta.scopes
        # Always present — required for refresh tokens to work
        assert "offline_access" in meta.scopes

    def test_intune_has_write_scope(self) -> None:
        """Intune needs ReadWrite to push policies."""
        meta = get_provider(IntegrationProvider.MICROSOFT_INTUNE)
        assert any("ReadWrite" in s for s in meta.scopes)

    def test_sentinel_is_available(self) -> None:
        meta = get_provider(IntegrationProvider.SENTINEL)
        assert meta.available is True
        assert meta.category == "data"
        assert meta.vendor == "microsoft"
        # v1 is a form-based connect wizard, not OAuth.
        assert meta.authorize_url is None
        assert meta.token_url is None
        assert meta.capabilities

    def test_logo_slugs_set(self) -> None:
        """Frontend depends on logo_slug being a non-empty string."""
        for m in list_providers():
            assert m.logo_slug
            assert " " not in m.logo_slug


class TestCostProviders:
    """Task 12 — five AI-spend cost connectors registered as provider tiles."""

    COST_PROVIDERS = (
        IntegrationProvider.ANTHROPIC,
        IntegrationProvider.OPENAI,
        IntegrationProvider.CURSOR,
        IntegrationProvider.GITHUB_COPILOT,
        IntegrationProvider.VERCEL,
    )

    def test_all_five_cost_providers_in_list(self) -> None:
        all_providers = {m.provider for m in list_providers()}
        for p in self.COST_PROVIDERS:
            assert p in all_providers, f"cost provider {p.value} missing from registry"

    def test_cost_providers_category(self) -> None:
        for p in self.COST_PROVIDERS:
            meta = get_provider(p)
            assert meta.category == "cost", (
                f"{p.value}: expected category='cost', got {meta.category!r}"
            )

    def test_cost_providers_available(self) -> None:
        for p in self.COST_PROVIDERS:
            meta = get_provider(p)
            assert meta.available is True, f"{p.value}: expected available=True"

    def test_cost_providers_no_oauth(self) -> None:
        """Cost connectors use API-key paste — no OAuth endpoints."""
        for p in self.COST_PROVIDERS:
            meta = get_provider(p)
            assert meta.authorize_url is None, (
                f"{p.value}: authorize_url should be None (API-key based)"
            )
            assert meta.token_url is None, f"{p.value}: token_url should be None (API-key based)"

    def test_cost_providers_non_empty_capabilities(self) -> None:
        for p in self.COST_PROVIDERS:
            meta = get_provider(p)
            assert meta.capabilities, f"{p.value}: capabilities must be non-empty"

    def test_get_provider_anthropic(self) -> None:
        meta = get_provider(IntegrationProvider.ANTHROPIC)
        assert meta.provider == IntegrationProvider.ANTHROPIC
        assert meta.category == "cost"
        assert meta.vendor == "anthropic"
        assert meta.available is True
        assert meta.authorize_url is None

    def test_cost_providers_not_onboarding_recommended(self) -> None:
        for p in self.COST_PROVIDERS:
            meta = get_provider(p)
            assert meta.onboarding_recommended is False, (
                f"{p.value}: should not be onboarding_recommended"
            )
