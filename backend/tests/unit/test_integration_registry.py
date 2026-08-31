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
        # AWS_BEDROCK is deliberately absent: cost slice 2 turned it into a
        # working push target, so it is no longer a placeholder.
        for p in (
            IntegrationProvider.AWS,
            IntegrationProvider.AWS_IAM_IDENTITY_CENTER,
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
        # + 5 pull cost connectors (Anthropic, OpenAI, Cursor, GitHub Copilot, Vercel) = 13
        # + Sentinel = 14
        # + 3 push cost providers (Azure AI Foundry, Bedrock, self-hosted) = 17.
        # AWS / GCP excluded (Coming Soon).
        assert len(available) == 17
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


class TestPushCostProviders:
    """Cost slice 2 — the three providers that report spend by pushing.

    They are the odd shape in this registry: category "cost" like the pull
    connectors, but with no connect flow of any kind. The customer instruments
    their own app and it POSTs to /api/v1/cost/usage; the Integration row is
    auto-provisioned on first push.
    """

    PUSH_PROVIDERS = (
        IntegrationProvider.AZURE_AI_FOUNDRY,
        IntegrationProvider.AWS_BEDROCK,
        IntegrationProvider.SELF_HOSTED_AI,
    )

    def test_registered_as_cost_providers(self) -> None:
        for p in self.PUSH_PROVIDERS:
            meta = get_provider(p)
            assert meta.category == "cost", (
                f"{p.value}: expected category='cost', got {meta.category!r}"
            )

    def test_available_so_they_do_not_render_as_coming_soon(self) -> None:
        """The regression this class exists for.

        `_to_card` maps `available=False` to status COMING_SOON, which the grid
        renders as a locked tile with a "Notify me" button. These providers are
        not coming — they work today, and an admin told to wait for one would
        never instrument their app.
        """
        for p in self.PUSH_PROVIDERS:
            assert get_provider(p).available is True, f"{p.value}: expected available=True"

    def test_no_oauth_endpoints(self) -> None:
        for p in self.PUSH_PROVIDERS:
            meta = get_provider(p)
            assert meta.authorize_url is None, f"{p.value}: push providers have no OAuth"
            assert meta.token_url is None, f"{p.value}: push providers have no OAuth"
            assert meta.scopes == []

    def test_capabilities_say_there_is_nothing_to_connect(self) -> None:
        """An admin looking at the tile must learn that from the tile.

        Otherwise the only signal is a Connect button that does not lead to a
        credential form, which reads as a broken integration rather than a
        different integration model.
        """
        for p in self.PUSH_PROVIDERS:
            meta = get_provider(p)
            assert meta.capabilities
            assert any("/api/v1/cost/usage" in c for c in meta.capabilities), (
                f"{p.value}: capabilities should name the push endpoint"
            )

    def test_every_push_cost_provider_has_an_integration_target(self) -> None:
        """Each pushable CostProvider maps to a registered IntegrationProvider.

        `resolve_integration` auto-provisions a row using this mapping, and
        `GET /api/v1/integrations/{id}` then calls `get_provider` on whatever it
        finds. A mapping to an unregistered provider is a KeyError -> HTTP 500
        on a row the customer's own push created.
        """
        from app.models.ai_cost_record import SelfHostedCostProvider
        from app.services.cost.self_hosted_ingest import _INTEGRATION_FOR_PROVIDER

        registered = {m.provider for m in list_providers()}
        for cost_provider in SelfHostedCostProvider:
            target = _INTEGRATION_FOR_PROVIDER[cost_provider]
            assert target in registered, (
                f"{cost_provider.value} maps to unregistered {target.value}"
            )

    def test_self_hosted_is_not_filed_under_a_cloud_vendor(self) -> None:
        """Generic self-hosted spend must not be attributed to Google.

        An earlier draft mapped it to GCP_VERTEX to avoid adding a constant,
        which would have put a vendor the customer may not use on their spend
        breakdown. Misattributing vendors is the one thing a cost tool cannot do.
        """
        from app.models.ai_cost_record import SelfHostedCostProvider
        from app.services.cost.self_hosted_ingest import _INTEGRATION_FOR_PROVIDER

        target = _INTEGRATION_FOR_PROVIDER[SelfHostedCostProvider.self_hosted]
        assert target is IntegrationProvider.SELF_HOSTED_AI
        assert get_provider(target).vendor == "other"


class TestProviderMetaSerialises:
    """Every registered provider must survive ProviderMetaPayload validation.

    This is not a theoretical invariant. `ProviderMetaPayload` used to restate
    the category / vendor vocabularies as its own Literals, and when the cost
    connectors were registered with category "cost" the schema was not updated
    — so `_meta_payload` raised ValidationError for all five, and
    `GET /api/v1/integrations` returned 500 for every tenant. The grid was down
    for anything, not just the cost tiles, because one bad card fails the whole
    response.

    The schema now imports the aliases from the registry, so a new category or
    vendor cannot drift. This test is the alarm if that coupling is ever undone.
    """

    def test_every_registered_provider_serialises(self) -> None:
        from app.routers.integrations import _meta_payload

        for meta in list_providers():
            payload = _meta_payload(meta)
            assert payload.provider == meta.provider
            assert payload.category == meta.category
            assert payload.vendor == meta.vendor

    def test_schema_and_registry_share_the_vocabularies(self) -> None:
        """Not just equal today — the same object.

        Equality would pass again the moment someone re-copies the literals and
        they happen to match; identity fails the instant the copy is made.
        """
        from app.schemas.integration import ProviderMetaPayload
        from app.services.integration_registry import ProviderCategory, ProviderVendor

        fields = ProviderMetaPayload.model_fields
        assert fields["category"].annotation is ProviderCategory
        assert fields["vendor"].annotation is ProviderVendor
