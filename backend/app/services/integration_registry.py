"""Integration provider registry — static metadata per provider.

The frontend `/dashboard/integrations` grid reads `list_providers()`
to render the cards. The OAuth flow reads `get_provider()` to look
up authorize URL, scopes, etc.

Microsoft providers all share the v2.0 Microsoft identity platform
(login.microsoftonline.com). The scope sets are the principle of
least privilege:

  Entra ID:    User.Read.All, Group.Read.All, Directory.Read.All
  Intune:      DeviceManagementConfiguration.Read.All,
               DeviceManagementApps.ReadWrite.All (writes for policy push)
  Purview:     AuditLog.Read.All, InformationProtectionPolicy.Read.All
  Defender:    SecurityEvents.Read.All
               (CASB-specific scopes require admin consent in M365)

Slack lives here as a read-only surface — the OAuth flow itself
stays in `app.services.slack_oauth` (PR #30).

AWS / GCP entries carry `status="COMING_SOON"` so the frontend
renders them as locked tiles with a "Notify me" button. The three
push-mode cost providers (Azure AI Foundry, Bedrock, self-hosted) are
the exception: they are `available=True` with no OAuth endpoints,
because the customer's app pushes usage rather than us pulling it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.integration import IntegrationProvider

# These two aliases are the single source of truth for the category / vendor
# vocabularies. `app.schemas.integration.ProviderMetaPayload` imports them
# rather than restating them: it used to hold its own copy, and when the cost
# connectors were added here and not there, every cost provider failed payload
# validation and took the whole /integrations grid down with a 500. A shared
# alias makes that class of drift unrepresentable.
ProviderCategory = Literal["identity", "device", "data", "communication", "cloud", "cost"]
ProviderVendor = Literal[
    "microsoft",
    "slack",
    "aws",
    "gcp",
    "anthropic",
    "openai",
    "cursor",
    "github",
    "vercel",
    "other",
]


@dataclass(frozen=True)
class ProviderMeta:
    """Static description of an integration provider."""

    provider: IntegrationProvider
    display_name: str
    short_name: str
    category: ProviderCategory
    vendor: ProviderVendor
    logo_slug: str  # frontend looks up `/logos/{slug}.svg`
    description: str
    # OAuth endpoints — None for placeholders.
    authorize_url: str | None
    token_url: str | None
    # Granted scopes (space-separated for Microsoft, comma for Slack).
    scopes: list[str]
    scope_separator: str
    # Whether the integration is wired in v0.1.
    available: bool
    # Whether this integration is recommended during onboarding.
    onboarding_recommended: bool
    # Free-text "what you get" — surfaced on the connect card.
    capabilities: list[str]


# Shared by the three push-mode cost providers (slice 2). Their value is the
# same regardless of which cloud the tokens were burned on, and stating "no
# connect flow" up front saves an admin hunting for a button that is not there.
_PUSH_CAPABILITIES = [
    "Report per-call token usage from your own AI apps",
    "Cost derived from a price book and badged as an estimate",
    "No connect flow — your app POSTs to /api/v1/cost/usage",
]


# ─── Microsoft providers ─────────────────────────────────────────────


MICROSOFT_AUTHORIZE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
MICROSOFT_TOKEN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


_ENTRA = ProviderMeta(
    provider=IntegrationProvider.MICROSOFT_ENTRA_ID,
    display_name="Microsoft Entra ID",
    short_name="Entra ID",
    category="identity",
    vendor="microsoft",
    logo_slug="microsoft-entra",
    description=(
        "Single sign-on, user / group / department sync. The recommended "
        "first connection — every other Microsoft integration uses the "
        "same M365 tenant."
    ),
    authorize_url=MICROSOFT_AUTHORIZE,
    token_url=MICROSOFT_TOKEN,
    scopes=[
        "openid",
        "profile",
        "offline_access",
        "User.Read.All",
        "Group.Read.All",
        "Directory.Read.All",
    ],
    scope_separator=" ",
    available=True,
    onboarding_recommended=True,
    capabilities=[
        "Sign in to atlas with your work account",
        "Sync users + groups for survey audience filters",
        "Department membership powers the registry catalogue",
    ],
)


_INTUNE = ProviderMeta(
    provider=IntegrationProvider.MICROSOFT_INTUNE,
    display_name="Microsoft Intune",
    short_name="Intune",
    category="device",
    vendor="microsoft",
    logo_slug="microsoft-intune",
    description=(
        "Push redact / coach / block policies to managed endpoints. "
        "Required for the /dashboard/policies *Push via Intune* affordance."
    ),
    authorize_url=MICROSOFT_AUTHORIZE,
    token_url=MICROSOFT_TOKEN,
    scopes=[
        "openid",
        "profile",
        "offline_access",
        "DeviceManagementConfiguration.Read.All",
        "DeviceManagementApps.ReadWrite.All",
    ],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Push Promptly policies to enrolled Windows / macOS devices",
        "Read device-group membership for policy scoping",
        "Surface enrolment health in /dashboard/deployment",
    ],
)


_PURVIEW = ProviderMeta(
    provider=IntegrationProvider.MICROSOFT_PURVIEW,
    display_name="Microsoft Purview",
    short_name="Purview",
    category="data",
    vendor="microsoft",
    logo_slug="microsoft-purview",
    description=(
        "Pull DLP events + data-classification context so atlas's Activity "
        "Log shows tenant-side audit trail alongside Promptly-agent events."
    ),
    authorize_url=MICROSOFT_AUTHORIZE,
    token_url=MICROSOFT_TOKEN,
    scopes=[
        "openid",
        "profile",
        "offline_access",
        "AuditLog.Read.All",
        "InformationProtectionPolicy.Read.All",
    ],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Ingest Purview DLP events into the Activity Log",
        "Show Purview classifier hits alongside Promptly redactions",
        "Cross-reference sensitivity labels in /dashboard/registry",
    ],
)


_DEFENDER_CASB = ProviderMeta(
    provider=IntegrationProvider.MICROSOFT_DEFENDER_CASB,
    display_name="Microsoft Defender for Cloud Apps",
    short_name="Defender CASB",
    category="data",
    vendor="microsoft",
    logo_slug="microsoft-defender",
    description=(
        "Cloud-app discovery feed. Surface unsanctioned AI tools detected "
        "by Defender in atlas's /dashboard/discover Shadow AI tile."
    ),
    authorize_url=MICROSOFT_AUTHORIZE,
    token_url=MICROSOFT_TOKEN,
    scopes=[
        "openid",
        "profile",
        "offline_access",
        "SecurityEvents.Read.All",
    ],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull Defender's cloud-app discovery feed",
        "Cross-reference shadow AI detection with Promptly's findings",
        "Auto-suggest survey audiences from discovered users",
    ],
)


# ─── Slack — surfaces the existing SlackWorkspace ────────────────────


_SLACK = ProviderMeta(
    provider=IntegrationProvider.SLACK,
    display_name="Slack",
    short_name="Slack",
    category="communication",
    vendor="slack",
    logo_slug="slack",
    description=(
        "DM-based survey bot for AI use-case registration. Required for "
        "the /dashboard/registry *Send survey* dispatch flow."
    ),
    authorize_url="https://slack.com/oauth/v2/authorize",
    token_url="https://slack.com/api/oauth.v2.access",
    scopes=[
        "chat:write",
        "im:write",
        "im:read",
        "users:read",
        "users:read.email",
    ],
    scope_separator=",",
    available=True,
    onboarding_recommended=True,
    capabilities=[
        "DM employees with the 5-question registration survey",
        "Block Kit interactive components — completion rate >70%",
        "STOP opt-out + GDPR right-to-erasure built in",
    ],
)


# ─── Non-Microsoft MDMs (Coming Soon — see docs/mdm-strategy.md) ────


_JAMF_PRO = ProviderMeta(
    provider=IntegrationProvider.JAMF_PRO,
    display_name="Jamf Pro",
    short_name="Jamf",
    category="device",
    vendor="other",
    logo_slug="jamf",
    description=(
        "Apple-focused MDM. Dominant in design, education, and "
        "creative shops. Atlas pulls the Mac roster + compliance "
        "state via the Jamf Pro API; force-installs the Promptly "
        "extension via Configuration Profile."
    ),
    # Jamf uses Basic-auth-for-bearer rather than OAuth — the connect
    # flow is a form, not a redirect. authorize_url stays None.
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull managed Mac inventory + compliance state",
        "Force-install Promptly via Configuration Profile",
        "Cross-MDM Endpoint Compliance reporting",
    ],
)


_KANDJI = ProviderMeta(
    provider=IntegrationProvider.KANDJI,
    display_name="Kandji",
    short_name="Kandji",
    category="device",
    vendor="other",
    logo_slug="kandji",
    description=(
        "Modern Apple MDM, popular with Series A-C startups. Atlas "
        "pulls the Mac / iPhone / iPad roster via the Kandji API "
        "and pushes the Promptly extension via Custom Profile."
    ),
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull managed Apple device inventory across macOS / iOS / iPadOS",
        "Custom Profile for browser extension force-install",
        "Compliance state surfaced alongside Intune + Jamf devices",
    ],
)


_JUMPCLOUD = ProviderMeta(
    provider=IntegrationProvider.JUMPCLOUD,
    display_name="JumpCloud",
    short_name="JumpCloud",
    category="device",
    vendor="other",
    logo_slug="jumpcloud",
    description=(
        "Directory + MDM combined; cross-platform; SMB-friendly. "
        "Atlas pulls the system roster (Mac / Windows / Linux) "
        "via the JumpCloud Directory API."
    ),
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull managed systems across macOS / Windows / Linux",
        "Cross-platform Command for browser extension push",
        "Compliance surfaced alongside Intune + Jamf + Kandji devices",
    ],
)


# ─── Placeholders ────────────────────────────────────────────────────


_AWS = ProviderMeta(
    provider=IntegrationProvider.AWS,
    display_name="Amazon Web Services",
    short_name="AWS",
    category="cloud",
    vendor="aws",
    logo_slug="aws",
    description="AWS-side AI governance integrations.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=False,
    onboarding_recommended=False,
    capabilities=["Coming in v0.2"],
)


_AWS_IAM_IC = ProviderMeta(
    provider=IntegrationProvider.AWS_IAM_IDENTITY_CENTER,
    display_name="AWS IAM Identity Center",
    short_name="IAM Identity Center",
    category="identity",
    vendor="aws",
    logo_slug="aws-iam-identity-center",
    description="SSO + user sync (AWS-managed Active Directory alternative).",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=False,
    onboarding_recommended=False,
    capabilities=["Coming in v0.2"],
)


# Bedrock stopped being a placeholder in cost slice 2: it is one of the three
# push targets below. Guardrails telemetry is still v0.2.
_AWS_BEDROCK = ProviderMeta(
    provider=IntegrationProvider.AWS_BEDROCK,
    display_name="AWS Bedrock",
    short_name="Bedrock",
    category="cost",
    vendor="aws",
    logo_slug="aws-bedrock",
    description=(
        "Report token usage from your own Bedrock apps into the AI Spend "
        "dashboard. Your app pushes; there is nothing to connect."
    ),
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=_PUSH_CAPABILITIES,
)


_GCP = ProviderMeta(
    provider=IntegrationProvider.GCP,
    display_name="Google Cloud Platform",
    short_name="GCP",
    category="cloud",
    vendor="gcp",
    logo_slug="gcp",
    description="GCP-side AI governance integrations.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=False,
    onboarding_recommended=False,
    capabilities=["Coming in v0.2"],
)


_GCP_WORKSPACE = ProviderMeta(
    provider=IntegrationProvider.GCP_WORKSPACE,
    display_name="Google Workspace",
    short_name="Workspace",
    category="identity",
    vendor="gcp",
    logo_slug="google-workspace",
    description="SSO + user / OU sync from Workspace admin.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=False,
    onboarding_recommended=False,
    capabilities=["Coming in v0.2"],
)


_GCP_VERTEX = ProviderMeta(
    provider=IntegrationProvider.GCP_VERTEX,
    display_name="Google Vertex AI",
    short_name="Vertex AI",
    category="data",
    vendor="gcp",
    logo_slug="gcp-vertex",
    description="Vertex AI model usage + safety telemetry.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=False,
    onboarding_recommended=False,
    capabilities=["Coming in v0.2"],
)


# ─── AI Cost connectors (API-key based, no OAuth) ────────────────────


_ANTHROPIC = ProviderMeta(
    provider=IntegrationProvider.ANTHROPIC,
    display_name="Anthropic",
    short_name="Anthropic",
    category="cost",
    vendor="anthropic",
    logo_slug="anthropic",
    description="Pull daily token usage and spend from the Anthropic Console API into the AI Spend dashboard.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull daily token/usage cost into the AI Spend dashboard",
        "Break down spend by model (Claude Opus, Sonnet, Haiku)",
        "Attribute cost to API keys and workspaces",
    ],
)


_OPENAI = ProviderMeta(
    provider=IntegrationProvider.OPENAI,
    display_name="OpenAI",
    short_name="OpenAI",
    category="cost",
    vendor="openai",
    logo_slug="openai",
    description="Pull daily token usage and spend from the OpenAI Usage API into the AI Spend dashboard.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull daily token/usage cost into the AI Spend dashboard",
        "Break down spend by model (GPT-4o, GPT-4, etc.)",
        "Attribute cost to projects and API keys",
    ],
)


_CURSOR = ProviderMeta(
    provider=IntegrationProvider.CURSOR,
    display_name="Cursor",
    short_name="Cursor",
    category="cost",
    vendor="cursor",
    logo_slug="cursor",
    description="Pull seat and usage spend from the Cursor Admin API into the AI Spend dashboard.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull monthly seat cost into the AI Spend dashboard",
        "Surface active vs inactive seat utilisation",
        "Attribute Cursor spend per team member",
    ],
)


_GITHUB_COPILOT = ProviderMeta(
    provider=IntegrationProvider.GITHUB_COPILOT,
    display_name="GitHub Copilot",
    short_name="Copilot",
    category="cost",
    vendor="github",
    logo_slug="github-copilot",
    description="Pull seat and usage spend from the GitHub Copilot Billing API into the AI Spend dashboard.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull monthly Copilot seat cost into the AI Spend dashboard",
        "Surface active vs inactive Copilot seat utilisation",
        "Attribute Copilot spend per team and organisation",
    ],
)


_VERCEL = ProviderMeta(
    provider=IntegrationProvider.VERCEL,
    display_name="Vercel",
    short_name="Vercel",
    category="cost",
    vendor="vercel",
    logo_slug="vercel",
    description="Pull AI SDK token usage and spend from the Vercel API into the AI Spend dashboard.",
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Pull daily AI SDK token/usage cost into the AI Spend dashboard",
        "Break down spend by deployment and project",
        "Surface Vercel AI Gateway usage alongside direct model spend",
    ],
)


# ─── SIEM connectors ─────────────────────────────────────────────────


_SENTINEL = ProviderMeta(
    provider=IntegrationProvider.SENTINEL,
    display_name="Microsoft Sentinel",
    short_name="Sentinel",
    category="data",
    vendor="microsoft",
    logo_slug="microsoft-sentinel",
    description=(
        "Stream AI-relevant activity (redactions, coaching, shadow-AI "
        "detections) into your Sentinel workspace so the SOC can pivot "
        "from investigations into Prompt Shields activity."
    ),
    # Not OAuth: the customer supplies an app registration in *their* Azure AD
    # tenant and the forwarder uses client credentials against it. The connect
    # wizard collects those coordinates directly — see
    # docs/integrations/microsoft-sentinel/spec.md §5.
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=[
        "Map Prompt Shields event types to a Sentinel custom table",
        "Stream prompt telemetry via the Azure Monitor Logs Ingestion API",
        "Replay undelivered batches from the dead-letter queue",
        "Correlate AI activity alongside Defender / Purview / Entra logs",
    ],
)


# ─── Self-hosted AI spend (cost slice 2 — push, not pull) ────────────
#
# These three have no connect flow at all, which makes them the odd shape in
# this registry. There is no per-tenant billing API to pull from: that spend
# sits on the customer's own Azure/AWS bill, aggregated by subscription and
# undifferentiated by application. So the customer instruments their app, it
# POSTs token usage to /api/v1/cost/usage, and the Integration row is
# auto-provisioned on first push.
#
# `available=True` matters here. A False would render them as locked
# "coming soon" tiles with a Notify-me button, and they are not coming — they
# work today. The frontend routes their Connect action to instructions rather
# than an OAuth or credential flow.


_AZURE_AI_FOUNDRY = ProviderMeta(
    provider=IntegrationProvider.AZURE_AI_FOUNDRY,
    display_name="Azure AI Foundry",
    short_name="AI Foundry",
    category="cost",
    vendor="microsoft",
    logo_slug="azure-ai-foundry",
    description=(
        "Report token usage from your own Azure AI Foundry apps into the AI "
        "Spend dashboard. Your app pushes; there is nothing to connect."
    ),
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=_PUSH_CAPABILITIES,
)


_SELF_HOSTED_AI = ProviderMeta(
    provider=IntegrationProvider.SELF_HOSTED_AI,
    display_name="Self-hosted models",
    short_name="Self-hosted",
    category="cost",
    vendor="other",
    logo_slug="self-hosted-ai",
    description=(
        "Report token usage from models you run yourself — an on-prem vLLM "
        "box, a self-managed gateway — into the AI Spend dashboard."
    ),
    authorize_url=None,
    token_url=None,
    scopes=[],
    scope_separator=" ",
    available=True,
    onboarding_recommended=False,
    capabilities=_PUSH_CAPABILITIES,
)


# ─── Registry ────────────────────────────────────────────────────────


_REGISTRY: dict[IntegrationProvider, ProviderMeta] = {
    p.provider: p
    for p in [
        _ENTRA,
        _INTUNE,
        _PURVIEW,
        _DEFENDER_CASB,
        _SLACK,
        _JAMF_PRO,
        _KANDJI,
        _JUMPCLOUD,
        _AWS,
        _AWS_IAM_IC,
        _AWS_BEDROCK,
        _GCP,
        _GCP_WORKSPACE,
        _GCP_VERTEX,
        _ANTHROPIC,
        _OPENAI,
        _CURSOR,
        _GITHUB_COPILOT,
        _VERCEL,
        _SENTINEL,
        _AZURE_AI_FOUNDRY,
        _SELF_HOSTED_AI,
    ]
}


def list_providers(*, available_only: bool = False) -> list[ProviderMeta]:
    rows = list(_REGISTRY.values())
    if available_only:
        rows = [r for r in rows if r.available]
    return rows


def get_provider(provider: IntegrationProvider) -> ProviderMeta:
    return _REGISTRY[provider]


def onboarding_recommended() -> list[ProviderMeta]:
    """Providers the onboarding wizard suggests by default.

    Currently Entra ID + Slack — both unlock major downstream value
    (SSO + user sync; survey bot dispatch).
    """
    return [p for p in _REGISTRY.values() if p.onboarding_recommended]
