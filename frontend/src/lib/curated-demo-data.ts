// Curated demo seeds. Production pages must gate this data via
// isDemoFallbackEnabled() from ./demo-mode before rendering it.
// Single source of truth for the curated dashboard's pinned numbers.
//
// Every cross-page count lives here — the Overview, Activity Log,
// Coaching, Policies, Deployment, and Audit & Report pages all read
// from this module. When cursor's M2 lands, replace the static values
// with async loaders against /api/v1/*. The shapes stay identical.
//
// The pattern is lifted from security-platform-demo's
// `utils/demoData.ts` (validated by the May 7 Weinberg call). The
// module-load assertion at the bottom enforces dept headcount
// reconciliation — if we ever forget to update one place when adding
// a department, the page crashes loudly instead of silently lying.
//
// Source brief: docs/feedbacks/curated-demo-spec.md
// Consolidation IA: docs/dashboard-consolidation.md

export const DEMO_DATA_BANNER = "Demo data"

// ─── Org constants ───────────────────────────────────────────────────

export const ORG = {
  name: "Sample Foundation",
  totalUsers: 80,
  activeUsers: 78,
  reportingPeriodDays: 30,
} as const

export const STACK_BADGES = [
  "Microsoft 365 E5",
  "Copilot Premium",
  "ChatGPT Business",
  "Intune",
  "SentinelOne",
] as const

// ─── Departments — must sum to ORG.totalUsers ────────────────────────

export interface Department {
  name: string
  shortLabel?: string
  users: number
}

export const DEPARTMENTS: Department[] = [
  { name: "Grants Management", users: 12 },
  { name: "Program Officer — Housing", shortLabel: "PO Housing", users: 8 },
  { name: "Program Officer — Health", shortLabel: "PO Health", users: 7 },
  { name: "Program Officer — Education", shortLabel: "PO Education", users: 6 },
  { name: "Program Officer — Jobs", shortLabel: "PO Jobs", users: 5 },
  { name: "Program Officer — Community Services", shortLabel: "PO Community", users: 5 },
  { name: "Operations / Admin", users: 9 },
  { name: "Finance", users: 8 },
  { name: "Communications", users: 6 },
  { name: "Executive Office", users: 4 },
  { name: "HR", users: 4 },
  { name: "Legal & Compliance", users: 3 },
  { name: "IT", users: 3 },
]

// ─── 30-day counts (the Overview / Coaching / Audit cross-page set) ──

export const COUNTS_30D = {
  totalPrompts: 4500,
  successfulPromptsTrendPct: 10, // +10% MoM
  redactedPrompts: 412,
  coachingNudges: 1247,
  riskyPromptsReductionPct: 20, // −20% MoM
  biasFlaggedPrompts: 9,
  highSeverityEvents: 2,
} as const

// ─── AI tool usage mix (Overview donut) ──────────────────────────────

export interface ToolUsage {
  tool: string
  sharePct: number
  isShadow: boolean
  /// Brand colour for the donut slice. Approximated from each vendor's
  /// public mark.
  color: string
}

export const TOOL_USAGE: ToolUsage[] = [
  { tool: "Microsoft Copilot Premium", sharePct: 64, isShadow: false, color: "#0078D4" },
  { tool: "ChatGPT Business", sharePct: 22, isShadow: false, color: "#10A37F" },
  { tool: "Claude", sharePct: 8, isShadow: true, color: "#D97757" },
  { tool: "Gemini", sharePct: 4, isShadow: true, color: "#4285F4" },
  { tool: "Perplexity", sharePct: 2, isShadow: true, color: "#1FB8CD" },
]

// ─── Activity events (Activity Log seed) ─────────────────────────────

export type ActivityEventType =
  | "Redacted"
  | "Anonymised"
  | "Blocked"
  | "Coached"
  | "Bias-flagged"

export type ActivitySeverity = "Low" | "Medium" | "High"

export interface ActivityEvent {
  id: string
  timestamp: string // ISO
  user: string // tagged "[DEMO]" — fictional placeholder
  department: string
  tool: string
  eventType: ActivityEventType
  sensitiveType: string
  detail: string
  severity: ActivitySeverity
}

const T0 = "2026-04-30T09:00:00Z"
function iso(offsetMin: number): string {
  const t = new Date(T0)
  t.setMinutes(t.getMinutes() + offsetMin)
  return t.toISOString()
}

export const ACTIVITY_EVENTS: ActivityEvent[] = [
  {
    id: "evt-001",
    timestamp: iso(0),
    user: "L. Park [DEMO]",
    department: "Grants Management",
    tool: "ChatGPT",
    eventType: "Redacted",
    sensitiveType: "PII (SSN, EIN)",
    detail: "Pasted grantee SSN + EIN drafting MOU",
    severity: "Medium",
  },
  {
    id: "evt-002",
    timestamp: iso(-15),
    user: "M. Hayes [DEMO]",
    department: "Program Officer — Housing",
    tool: "Copilot",
    eventType: "Redacted",
    sensitiveType: "PII (address, PHI)",
    detail: "Beneficiary home address + medical condition in grant memo",
    severity: "Medium",
  },
  {
    id: "evt-003",
    timestamp: iso(-32),
    user: "R. Cohen [DEMO]",
    department: "Communications",
    tool: "Copilot",
    eventType: "Anonymised",
    sensitiveType: "Donor PII",
    detail: "Donor list pasted to draft thank-you newsletter",
    severity: "Low",
  },
  {
    id: "evt-004",
    timestamp: iso(-58),
    user: "J. Tanaka [DEMO]",
    department: "HR",
    tool: "Claude",
    eventType: "Blocked",
    sensitiveType: "Compensation",
    detail: "Salary review for VP-level employee",
    severity: "High",
  },
  {
    id: "evt-005",
    timestamp: iso(-90),
    user: "S. Diallo [DEMO]",
    department: "Finance",
    tool: "ChatGPT",
    eventType: "Blocked",
    sensitiveType: "Financial / banking",
    detail: "Bank routing info in vendor dispute",
    severity: "High",
  },
  {
    id: "evt-006",
    timestamp: iso(-125),
    user: "A. Weiss [DEMO]",
    department: "Executive Office",
    tool: "Perplexity",
    eventType: "Redacted",
    sensitiveType: "PHI",
    detail: "Grant decision summary w/ health condition",
    severity: "Medium",
  },
  {
    id: "evt-007",
    timestamp: iso(-160),
    user: "P. Nguyen [DEMO]",
    department: "Program Officer — Health",
    tool: "Copilot",
    eventType: "Coached",
    sensitiveType: "—",
    detail: "Drafted on-brand reply, no PII risk after nudge",
    severity: "Low",
  },
  {
    id: "evt-008",
    timestamp: iso(-200),
    user: "K. Brooks [DEMO]",
    department: "Program Officer — Education",
    tool: "ChatGPT",
    eventType: "Bias-flagged",
    sensitiveType: "Protected characteristics",
    detail: "Grant-scoring prompt: 'applicants from XYZ neighborhood'",
    severity: "Medium",
  },
  {
    id: "evt-009",
    timestamp: iso(-245),
    user: "D. Okafor [DEMO]",
    department: "Grants Management",
    tool: "Claude",
    eventType: "Bias-flagged",
    sensitiveType: "Protected characteristics",
    detail: "Scoring prompt referenced applicant's age",
    severity: "Medium",
  },
  {
    id: "evt-010",
    timestamp: iso(-290),
    user: "T. Mendel [DEMO]",
    department: "Legal & Compliance",
    tool: "Copilot",
    eventType: "Redacted",
    sensitiveType: "PII (financial)",
    detail: "990-PF draft w/ grantee finances",
    severity: "Medium",
  },
  {
    id: "evt-011",
    timestamp: iso(-340),
    user: "G. Marsh [DEMO]",
    department: "Operations / Admin",
    tool: "Copilot",
    eventType: "Coached",
    sensitiveType: "—",
    detail: "Switched to enterprise Copilot per nudge",
    severity: "Low",
  },
  {
    id: "evt-012",
    timestamp: iso(-385),
    user: "B. Reilly [DEMO]",
    department: "Program Officer — Jobs",
    tool: "ChatGPT",
    eventType: "Redacted",
    sensitiveType: "PII (SSN)",
    detail: "Beneficiary SSN in workforce-program memo",
    severity: "Medium",
  },
  {
    id: "evt-013",
    timestamp: iso(-420),
    user: "F. Iyer [DEMO]",
    department: "Communications",
    tool: "Gemini",
    eventType: "Coached",
    sensitiveType: "—",
    detail: "Prompted to use approved tool for confidential queries",
    severity: "Low",
  },
  {
    id: "evt-014",
    timestamp: iso(-470),
    user: "N. Olsen [DEMO]",
    department: "Program Officer — Community Services",
    tool: "Copilot",
    eventType: "Bias-flagged",
    sensitiveType: "Protected characteristics",
    detail: "Scoring prompt cited 'low-income zip codes' as eligibility filter",
    severity: "Medium",
  },
  {
    id: "evt-015",
    timestamp: iso(-510),
    user: "H. Castillo [DEMO]",
    department: "HR",
    tool: "ChatGPT",
    eventType: "Redacted",
    sensitiveType: "PII (DOB)",
    detail: "DOB in benefits eligibility worksheet",
    severity: "Low",
  },
  {
    id: "evt-016",
    timestamp: iso(-560),
    user: "Y. Kowalski [DEMO]",
    department: "Finance",
    tool: "Copilot",
    eventType: "Redacted",
    sensitiveType: "Financial / banking",
    detail: "Vendor IBAN in payment-reconciliation prompt",
    severity: "Medium",
  },
  {
    id: "evt-017",
    timestamp: iso(-610),
    user: "M. Bekele [DEMO]",
    department: "Program Officer — Health",
    tool: "ChatGPT",
    eventType: "Bias-flagged",
    sensitiveType: "Protected characteristics",
    detail: "Beneficiary scoring referenced ethnicity proxy",
    severity: "Medium",
  },
  {
    id: "evt-018",
    timestamp: iso(-660),
    user: "C. Liu [DEMO]",
    department: "Grants Management",
    tool: "Copilot",
    eventType: "Redacted",
    sensitiveType: "PII (address)",
    detail: "Grantee mailing address in MOU revision",
    severity: "Low",
  },
  {
    id: "evt-019",
    timestamp: iso(-720),
    user: "V. Hassan [DEMO]",
    department: "IT",
    tool: "Claude",
    eventType: "Coached",
    sensitiveType: "—",
    detail: "Nudged away from a public LLM for help-desk transcripts",
    severity: "Low",
  },
  {
    id: "evt-020",
    timestamp: iso(-790),
    user: "E. Bach [DEMO]",
    department: "Executive Office",
    tool: "Copilot",
    eventType: "Redacted",
    sensitiveType: "PII (donor)",
    detail: "Donor cultivation memo with PII",
    severity: "Low",
  },
  {
    id: "evt-021",
    timestamp: iso(-840),
    user: "R. Sato [DEMO]",
    department: "Program Officer — Education",
    tool: "Perplexity",
    eventType: "Redacted",
    sensitiveType: "PII (PHI)",
    detail: "Beneficiary health condition in narrative report",
    severity: "Low",
  },
  {
    id: "evt-022",
    timestamp: iso(-905),
    user: "T. Nakagawa [DEMO]",
    department: "Operations / Admin",
    tool: "Copilot",
    eventType: "Redacted",
    sensitiveType: "PII (SSN)",
    detail: "Vendor TIN in contract draft",
    severity: "Medium",
  },
  {
    id: "evt-023",
    timestamp: iso(-960),
    user: "L. Pham [DEMO]",
    department: "Legal & Compliance",
    tool: "Copilot",
    eventType: "Redacted",
    sensitiveType: "PII",
    detail: "Litigation hold contact info pasted into draft",
    severity: "Medium",
  },
  {
    id: "evt-024",
    timestamp: iso(-1015),
    user: "X. Ahmed [DEMO]",
    department: "Program Officer — Housing",
    tool: "Claude",
    eventType: "Bias-flagged",
    sensitiveType: "Protected characteristics",
    detail: "Tenant-screening prompt: race/ethnicity proxy",
    severity: "Medium",
  },
  {
    id: "evt-025",
    timestamp: iso(-1080),
    user: "Q. Berhanu [DEMO]",
    department: "Communications",
    tool: "ChatGPT",
    eventType: "Coached",
    sensitiveType: "—",
    detail: "Press-release nudge to remove embargoed donor name",
    severity: "Low",
  },
]

// ─── Coaching extras (Coaching page) ─────────────────────────────────

export interface CoachingNudge {
  title: string
  deliveries: number
}

export const TOP_NUDGES: CoachingNudge[] = [
  { title: "Don't paste SSN — use anonymised reference", deliveries: 312 },
  { title: "Try this prompt structure for grant memos", deliveries: 264 },
  {
    title: "Switch to enterprise Copilot for confidential queries",
    deliveries: 198,
  },
]

/// Active-user share by department, 0–100. Drives the Coaching
/// page's adoption-by-dept bar.
export const ADOPTION_BY_DEPT: { name: string; percent: number }[] = [
  { name: "Communications", percent: 88 },
  { name: "IT", percent: 85 },
  { name: "Operations / Admin", percent: 82 },
  { name: "Executive Office", percent: 80 },
  { name: "Program Officer — Education", percent: 78 },
  { name: "Grants Management", percent: 76 },
  { name: "Program Officer — Health", percent: 74 },
  { name: "Program Officer — Jobs", percent: 70 },
  { name: "Program Officer — Housing", percent: 68 },
  { name: "Program Officer — Community Services", percent: 66 },
  { name: "Finance", percent: 62 },
  { name: "HR", percent: 58 },
  { name: "Legal & Compliance", percent: 52 },
]

// ─── Policies (Policies page) ────────────────────────────────────────

export type PolicyRule = "redact" | "coach" | "block"
export type PolicyStatus = "Active" | "Draft"

export interface CuratedPolicy {
  id: string
  name: string
  scope: string[] // department names
  rule: PolicyRule
  sensitiveType: string
  status: PolicyStatus
  lastUpdated: string // ISO date
}

export const POLICIES: CuratedPolicy[] = [
  {
    id: "pol-001",
    name: "No grantee PII to external LLMs",
    scope: ["Grants Management"],
    rule: "redact",
    sensitiveType: "PII (SSN, EIN, address)",
    status: "Active",
    lastUpdated: "2026-04-12",
  },
  {
    id: "pol-002",
    name: "No salary or comp data to any AI",
    scope: ["HR", "Executive Office"],
    rule: "block",
    sensitiveType: "Compensation",
    status: "Active",
    lastUpdated: "2026-04-08",
  },
  {
    id: "pol-003",
    name: "Bias check on grant-scoring prompts",
    scope: [
      "Program Officer — Housing",
      "Program Officer — Health",
      "Program Officer — Education",
      "Program Officer — Jobs",
      "Program Officer — Community Services",
    ],
    rule: "coach",
    sensitiveType: "Protected characteristics",
    status: "Active",
    lastUpdated: "2026-04-01",
  },
  {
    id: "pol-004",
    name: "Donor data redaction",
    scope: ["Communications"],
    rule: "redact",
    sensitiveType: "Donor PII",
    status: "Draft",
    lastUpdated: "2026-04-22",
  },
]

// ─── Deployment (Deployment page) ────────────────────────────────────

export interface PlatformBreakdown {
  name: string
  count: number
}

export interface OfflineAgent {
  user: string
  reason: string
  lastSeen: string
}

export const DEPLOYMENT = {
  agentsOnline: 78,
  agentsTotal: 80,
  agentVersion: "v1.4.2",
  versionStatus: "Up to date",
  lastIntuneSync: "14 min ago",
  platforms: [
    { name: "Windows ARM 64 (Surface)", count: 76 },
    { name: "Windows x64", count: 1 },
    { name: "macOS Sonoma", count: 1 },
  ] as PlatformBreakdown[],
  offlineAgents: [
    {
      user: "B. Tanaka [DEMO]",
      reason: "Offboarded 2026-04-29",
      lastSeen: "7 days ago",
    },
    {
      user: "C. Reilly [DEMO]",
      reason: "Surface in Hawaii office, sleep mode > 7 days",
      lastSeen: "9 days ago",
    },
  ] as OfflineAgent[],
}

// ─── Frameworks (Audit & Report page) ────────────────────────────────

export interface RegulatoryFramework {
  code: string
  note: string
}

export const US_FRAMEWORKS: RegulatoryFramework[] = [
  {
    code: "NIST AI RMF 1.0",
    note: "Voluntary; the right reference for credible US governance language.",
  },
  {
    code: "IRS 990-PF",
    note: "Private foundation board fiduciary duty includes AI risk oversight.",
  },
  {
    code: "MD MPIPA",
    note: "Maryland breach-notification duty for MD residents' PII.",
  },
  {
    code: "MD MODPA",
    note: "Maryland consumer data rights — drives consumer/grantee identifier redaction.",
  },
  {
    code: "CA AI Transparency Act SB 942",
    note: "Effective Jan 2026; applies if generating AI content for CA audiences.",
  },
  {
    code: "IL HB 3773",
    note: "Applies if you have IL-based staff.",
  },
  {
    code: "TX TDPSA",
    note: "Applies if you have TX consumers.",
  },
]

export const EU_AI_ACT_NOTE =
  "EU AI Act applicability depends on jurisdiction — adjust per customer."

// ─── Use-case registry (docs/use-case-survey-bot.md §4) ──────────────

export type UseCaseStatus = "Active" | "Review" | "Draft" | "Retired"
export type UseCaseRiskTier = "Low" | "Medium" | "High"
export type UseCaseSource = "bot" | "form" | "shadow-promote"

export interface RegisteredUseCase {
  id: string
  title: string
  tool: string
  department: string
  owner: string
  status: UseCaseStatus
  riskTier: UseCaseRiskTier
  source: UseCaseSource
  dataClasses: string[]
  registeredAt: string // ISO date
}

export const REGISTERED_USE_CASES: RegisteredUseCase[] = [
  {
    id: "uc-001",
    title: "Grant memo drafting",
    tool: "Microsoft Copilot",
    department: "Grants Management",
    owner: "L. Park [DEMO]",
    status: "Active",
    riskTier: "Low",
    source: "form",
    dataClasses: ["grantee_metadata"],
    registeredAt: "2026-03-04",
  },
  {
    id: "uc-002",
    title: "Beneficiary intake summarisation",
    tool: "Microsoft Copilot",
    department: "Program Officer — Housing",
    owner: "M. Hayes [DEMO]",
    status: "Active",
    riskTier: "Medium",
    source: "bot",
    dataClasses: ["beneficiary_pii", "phi"],
    registeredAt: "2026-04-12",
  },
  {
    id: "uc-003",
    title: "Press release first-draft",
    tool: "Microsoft Copilot",
    department: "Communications",
    owner: "R. Cohen [DEMO]",
    status: "Active",
    riskTier: "Low",
    source: "bot",
    dataClasses: ["donor_metadata"],
    registeredAt: "2026-04-22",
  },
  {
    id: "uc-004",
    title: "990-PF narrative assist",
    tool: "Microsoft Copilot",
    department: "Finance",
    owner: "S. Diallo [DEMO]",
    status: "Review",
    riskTier: "Medium",
    source: "bot",
    dataClasses: ["financial_data"],
    registeredAt: "2026-05-01",
  },
  {
    id: "uc-005",
    title: "Vendor analysis & comparison",
    tool: "ChatGPT",
    department: "Operations / Admin",
    owner: "G. Marsh [DEMO]",
    status: "Review",
    riskTier: "Medium",
    source: "shadow-promote",
    dataClasses: ["vendor_pii", "financial_data"],
    registeredAt: "2026-05-06",
  },
  {
    id: "uc-006",
    title: "Help-desk transcript triage",
    tool: "Claude",
    department: "IT",
    owner: "V. Hassan [DEMO]",
    status: "Draft",
    riskTier: "Low",
    source: "bot",
    dataClasses: ["employee_pii"],
    registeredAt: "2026-05-08",
  },
]

// Survey template — drives the Slack bot's question flow and the
// hosted email-fallback form. Lives in code today; lives in the
// SurveyTemplate table once cursor's M1 schema port lands.

export type SurveyQuestionType =
  | "yesNo"
  | "singleSelect"
  | "multiSelect"
  | "freeText"

export interface SurveyQuestion {
  id: string
  prompt: string
  type: SurveyQuestionType
  options?: string[]
  optional?: boolean
  /** Show this question only if a prior answer matches. */
  condition?: { question: string; equals?: string; includes?: string }
}

export interface SurveyTemplateRecord {
  id: string
  name: string
  version: string
  description: string
  questions: SurveyQuestion[]
}

export const USE_CASE_SURVEY_TEMPLATE: SurveyTemplateRecord = {
  id: "foundation-use-case-v1",
  name: "Foundation AI use case (default)",
  version: "v1",
  description:
    "5-question flow for foundation staff to register an AI use case. ~60–90s to complete via Slack DM.",
  questions: [
    {
      id: "uses-ai",
      prompt: "Have you used an AI tool for work in the last 7 days?",
      type: "singleSelect",
      options: ["Yes", "No", "Not sure"],
    },
    {
      id: "tools",
      prompt: "Which tool(s)? Pick all that apply.",
      type: "multiSelect",
      options: [
        "Microsoft Copilot",
        "ChatGPT",
        "Claude",
        "Gemini",
        "Perplexity",
        "Other",
      ],
      condition: { question: "uses-ai", equals: "Yes" },
    },
    {
      id: "tasks",
      prompt: "What for? Pick all that apply.",
      type: "multiSelect",
      options: [
        "Drafting documents",
        "Summarising",
        "Research",
        "Coding",
        "Data analysis",
        "Brainstorming",
        "Other",
      ],
      condition: { question: "uses-ai", equals: "Yes" },
    },
    {
      id: "data-classes",
      prompt: "Have you pasted any of the following into an AI tool?",
      type: "multiSelect",
      options: [
        "Customer / grantee PII",
        "Employee PII",
        "Financial data",
        "Proprietary code",
        "Strategy docs",
        "None of the above",
      ],
      condition: { question: "uses-ai", equals: "Yes" },
    },
    {
      id: "notes",
      prompt:
        "Anything else IT should know? (Optional — skip if not applicable.)",
      type: "freeText",
      optional: true,
      condition: { question: "uses-ai", equals: "Yes" },
    },
  ],
}

// ─── Survey delivery history (recent surveys card) ───────────────────

export interface SurveyDeliverySummary {
  id: string
  name: string
  sentAt: string // ISO date
  audienceLabel: string
  recipientCount: number
  completedCount: number
  newRegistrationCount: number
  channel: "slack" | "email"
}

export const RECENT_SURVEY_DELIVERIES: SurveyDeliverySummary[] = [
  {
    id: "sd-001",
    name: "Foundation Q2 registration",
    sentAt: "2026-05-08",
    audienceLabel: "All staff (80)",
    recipientCount: 42,
    completedCount: 31,
    newRegistrationCount: 5,
    channel: "slack",
  },
  {
    id: "sd-002",
    name: "Shadow Copilot detection — Operations",
    sentAt: "2026-05-06",
    audienceLabel: "Operations / Admin (9)",
    recipientCount: 9,
    completedCount: 7,
    newRegistrationCount: 1,
    channel: "slack",
  },
  {
    id: "sd-003",
    name: "Grants Mgmt — quarterly check-in",
    sentAt: "2026-04-04",
    audienceLabel: "Grants Management (12)",
    recipientCount: 12,
    completedCount: 11,
    newRegistrationCount: 3,
    channel: "slack",
  },
]

// ─── Integrations catalogue (docs/integrations.md) ───────────────────
//
// Mirror of backend's `app.services.integration_registry.list_providers()`.
// Frontend reads from here until the live `/api/v1/integrations`
// endpoint is wired in PR 3 (onboarding). When that lands, swap the
// import for `await api.listIntegrations()`.

export type IntegrationStatus =
  | "NOT_CONNECTED"
  | "CONNECTED"
  | "ERROR"
  | "DISABLED"
  | "COMING_SOON"

// Mirrors ProviderCategory / ProviderVendor in the backend's
// app/services/integration_registry.py, which the API schema imports directly.
// Adding a vendor here also needs a swatch in VENDOR_INITIAL_BG and adding a
// category needs a label + count in the integrations grid; both are exhaustive
// Records, so the compiler will say so.
export type IntegrationCategory =
  | "identity"
  | "device"
  | "data"
  | "communication"
  | "cloud"
  | "cost"

export type IntegrationVendor =
  | "microsoft"
  | "slack"
  | "aws"
  | "gcp"
  | "anthropic"
  | "openai"
  | "cursor"
  | "github"
  | "vercel"
  | "other"

export interface IntegrationProviderMeta {
  provider: string // backend enum value, e.g. "MICROSOFT_ENTRA_ID"
  displayName: string
  shortName: string
  category: IntegrationCategory
  vendor: IntegrationVendor
  logoSlug: string
  description: string
  capabilities: string[]
  available: boolean
  onboardingRecommended: boolean
}

export interface IntegrationCard {
  meta: IntegrationProviderMeta
  status: IntegrationStatus
  integrationId: string | null
  displayName: string
  externalId: string | null
  externalName: string | null
  scopes: string[]
  lastSyncedAt: string | null
  lastError: string | null
  connectedAt: string | null
}

export const INTEGRATION_CATALOGUE: IntegrationCard[] = [
  // Microsoft — the v0.1 focus.
  {
    meta: {
      provider: "MICROSOFT_ENTRA_ID",
      displayName: "Microsoft Entra ID",
      shortName: "Entra ID",
      category: "identity",
      vendor: "microsoft",
      logoSlug: "microsoft-entra",
      description:
        "Single sign-on, user / group / department sync. The recommended first connection — every other Microsoft integration uses the same M365 tenant.",
      capabilities: [
        "Sign in to atlas with your work account",
        "Sync users + groups for survey audience filters",
        "Department membership powers the registry catalogue",
      ],
      available: true,
      onboardingRecommended: true,
    },
    status: "CONNECTED",
    integrationId: "demo-entra",
    displayName: "Sample Foundation — M365 Tenant",
    externalId: "5a3b9b4c-7e2d-4e1f-9c8a-aad-tenant-id",
    externalName: "Sample Foundation",
    scopes: [
      "openid",
      "profile",
      "offline_access",
      "User.Read.All",
      "Group.Read.All",
      "Directory.Read.All",
    ],
    lastSyncedAt: "2026-05-10T14:32:00Z",
    lastError: null,
    connectedAt: "2026-04-12T09:18:00Z",
  },
  {
    meta: {
      provider: "MICROSOFT_INTUNE",
      displayName: "Microsoft Intune",
      shortName: "Intune",
      category: "device",
      vendor: "microsoft",
      logoSlug: "microsoft-intune",
      description:
        "Push redact / coach / block policies to managed endpoints. Required for the /dashboard/policies *Push via Intune* affordance.",
      capabilities: [
        "Push Promptly policies to enrolled Windows / macOS devices",
        "Read device-group membership for policy scoping",
        "Surface enrolment health in /dashboard/deployment",
      ],
      available: true,
      onboardingRecommended: false,
    },
    status: "NOT_CONNECTED",
    integrationId: null,
    displayName: "Microsoft Intune",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "MICROSOFT_PURVIEW",
      displayName: "Microsoft Purview",
      shortName: "Purview",
      category: "data",
      vendor: "microsoft",
      logoSlug: "microsoft-purview",
      description:
        "Pull DLP events + data-classification context so atlas's Activity Log shows tenant-side audit trail alongside Promptly-agent events.",
      capabilities: [
        "Ingest Purview DLP events into the Activity Log",
        "Show Purview classifier hits alongside Promptly redactions",
        "Cross-reference sensitivity labels in /dashboard/registry",
      ],
      available: true,
      onboardingRecommended: false,
    },
    status: "NOT_CONNECTED",
    integrationId: null,
    displayName: "Microsoft Purview",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "MICROSOFT_DEFENDER_CASB",
      displayName: "Microsoft Defender for Cloud Apps",
      shortName: "Defender CASB",
      category: "data",
      vendor: "microsoft",
      logoSlug: "microsoft-defender",
      description:
        "Cloud-app discovery feed. Surface unsanctioned AI tools detected by Defender in atlas's /dashboard/discover Shadow AI tile.",
      capabilities: [
        "Pull Defender's cloud-app discovery feed",
        "Cross-reference shadow AI detection with Promptly's findings",
        "Auto-suggest survey audiences from discovered users",
      ],
      available: true,
      onboardingRecommended: false,
    },
    status: "NOT_CONNECTED",
    integrationId: null,
    displayName: "Microsoft Defender for Cloud Apps",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  // Slack — surfaces the existing SlackWorkspace (PR #30).
  {
    meta: {
      provider: "SLACK",
      displayName: "Slack",
      shortName: "Slack",
      category: "communication",
      vendor: "slack",
      logoSlug: "slack",
      description:
        "DM-based survey bot for AI use-case registration. Required for the /dashboard/registry *Send survey* dispatch flow.",
      capabilities: [
        "DM employees with the 5-question registration survey",
        "Block Kit interactive components — completion rate >70%",
        "STOP opt-out + GDPR right-to-erasure built in",
      ],
      available: true,
      onboardingRecommended: true,
    },
    status: "CONNECTED",
    integrationId: "demo-slack",
    displayName: "Sample Foundation Slack",
    externalId: "T01234567",
    externalName: "samplefoundation",
    scopes: [
      "chat:write",
      "im:write",
      "im:read",
      "users:read",
      "users:read.email",
    ],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: "2026-04-15T11:02:00Z",
  },
  // Non-Microsoft MDMs — see docs/mdm-strategy.md.
  // Jamf Pro graduated from Coming Soon → available in the
  // Jamf-sync PR (jamf_api.py + jamf_sync.py + worker mode).
  {
    meta: {
      provider: "JAMF_PRO",
      displayName: "Jamf Pro",
      shortName: "Jamf",
      category: "device",
      vendor: "other",
      logoSlug: "jamf",
      description:
        "Apple-focused MDM. Atlas pulls the Mac roster + compliance state via the Jamf Pro API; force-installs the Promptly extension via Configuration Profile.",
      capabilities: [
        "Pull managed Mac inventory + compliance state",
        "Force-install Promptly via Configuration Profile",
        "Cross-MDM Endpoint Compliance reporting",
      ],
      available: true,
      onboardingRecommended: false,
    },
    status: "NOT_CONNECTED",
    integrationId: null,
    displayName: "Jamf Pro",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "KANDJI",
      displayName: "Kandji",
      shortName: "Kandji",
      category: "device",
      vendor: "other",
      logoSlug: "kandji",
      description:
        "Modern Apple MDM, popular with Series A-C startups. Atlas pulls the Mac / iPhone / iPad roster via the Kandji API and pushes the Promptly extension via Custom Profile.",
      capabilities: [
        "Pull managed Apple device inventory across macOS / iOS / iPadOS",
        "Custom Profile for browser extension force-install",
        "Compliance state surfaced alongside Intune + Jamf devices",
      ],
      available: true,
      onboardingRecommended: false,
    },
    status: "NOT_CONNECTED",
    integrationId: null,
    displayName: "Kandji",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "JUMPCLOUD",
      displayName: "JumpCloud",
      shortName: "JumpCloud",
      category: "device",
      vendor: "other",
      logoSlug: "jumpcloud",
      description:
        "Directory + MDM combined; cross-platform; SMB-friendly. Atlas pulls the system roster (Mac / Windows / Linux) via the JumpCloud Directory API.",
      capabilities: [
        "Pull managed systems across macOS / Windows / Linux",
        "Cross-platform Command for browser extension push",
        "Compliance surfaced alongside Intune + Jamf + Kandji devices",
      ],
      available: true,
      onboardingRecommended: false,
    },
    status: "NOT_CONNECTED",
    integrationId: null,
    displayName: "JumpCloud",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  // SIEM — see docs/integrations/microsoft-sentinel/spec.md.
  {
    meta: {
      provider: "SENTINEL",
      displayName: "Microsoft Sentinel",
      shortName: "Sentinel",
      category: "data",
      vendor: "microsoft",
      logoSlug: "microsoft-sentinel",
      description:
        "Stream AI-relevant activity (redactions, coaching, shadow-AI detections) into your Sentinel workspace so the SOC can pivot from investigations into Prompt Shields activity.",
      capabilities: [
        "Map Prompt Shields event types to a Sentinel custom table",
        "Live event stream preview before enabling real ingestion",
        "Correlate AI activity alongside Defender / Purview / Entra logs",
      ],
      available: true,
      onboardingRecommended: false,
    },
    status: "NOT_CONNECTED",
    integrationId: null,
    displayName: "Microsoft Sentinel",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  // Placeholders — v0.2.
  {
    meta: {
      provider: "AWS",
      displayName: "Amazon Web Services",
      shortName: "AWS",
      category: "cloud",
      vendor: "aws",
      logoSlug: "aws",
      description: "AWS-side AI governance integrations.",
      capabilities: ["Coming in v0.2"],
      available: false,
      onboardingRecommended: false,
    },
    status: "COMING_SOON",
    integrationId: null,
    displayName: "Amazon Web Services",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "AWS_IAM_IDENTITY_CENTER",
      displayName: "AWS IAM Identity Center",
      shortName: "IAM Identity Center",
      category: "identity",
      vendor: "aws",
      logoSlug: "aws-iam-identity-center",
      description: "SSO + user sync (AWS-managed Active Directory alternative).",
      capabilities: ["Coming in v0.2"],
      available: false,
      onboardingRecommended: false,
    },
    status: "COMING_SOON",
    integrationId: null,
    displayName: "AWS IAM Identity Center",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "AWS_BEDROCK",
      displayName: "AWS Bedrock",
      shortName: "Bedrock",
      category: "data",
      vendor: "aws",
      logoSlug: "aws-bedrock",
      description: "Bedrock model usage + guardrails telemetry.",
      capabilities: ["Coming in v0.2"],
      available: false,
      onboardingRecommended: false,
    },
    status: "COMING_SOON",
    integrationId: null,
    displayName: "AWS Bedrock",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "GCP",
      displayName: "Google Cloud Platform",
      shortName: "GCP",
      category: "cloud",
      vendor: "gcp",
      logoSlug: "gcp",
      description: "GCP-side AI governance integrations.",
      capabilities: ["Coming in v0.2"],
      available: false,
      onboardingRecommended: false,
    },
    status: "COMING_SOON",
    integrationId: null,
    displayName: "Google Cloud Platform",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "GCP_WORKSPACE",
      displayName: "Google Workspace",
      shortName: "Workspace",
      category: "identity",
      vendor: "gcp",
      logoSlug: "google-workspace",
      description: "SSO + user / OU sync from Workspace admin.",
      capabilities: ["Coming in v0.2"],
      available: false,
      onboardingRecommended: false,
    },
    status: "COMING_SOON",
    integrationId: null,
    displayName: "Google Workspace",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
  {
    meta: {
      provider: "GCP_VERTEX",
      displayName: "Google Vertex AI",
      shortName: "Vertex AI",
      category: "data",
      vendor: "gcp",
      logoSlug: "gcp-vertex",
      description: "Vertex AI model usage + safety telemetry.",
      capabilities: ["Coming in v0.2"],
      available: false,
      onboardingRecommended: false,
    },
    status: "COMING_SOON",
    integrationId: null,
    displayName: "Google Vertex AI",
    externalId: null,
    externalName: null,
    scopes: [],
    lastSyncedAt: null,
    lastError: null,
    connectedAt: null,
  },
]

// ─── §3.6 Discover — Shadow AI detections ────────────────────────────

export interface ShadowAppDetection {
  id: string
  appName: string
  appDomain: string
  appCategory: string
  riskScore: number // 0-10
  detectedUsers: number
  firstSeen: string // ISO date
  lastSeen: string
  status: "Detected" | "Under review" | "Approved" | "Blocked"
}

export const SHADOW_APP_DETECTIONS: ShadowAppDetection[] = [
  {
    id: "shadow-001",
    appName: "ChatGPT",
    appDomain: "chatgpt.com",
    appCategory: "Generative AI",
    riskScore: 7,
    detectedUsers: 18,
    firstSeen: "2026-04-12",
    lastSeen: "2026-05-10",
    status: "Approved",
  },
  {
    id: "shadow-002",
    appName: "Claude",
    appDomain: "claude.ai",
    appCategory: "Generative AI",
    riskScore: 6,
    detectedUsers: 7,
    firstSeen: "2026-04-15",
    lastSeen: "2026-05-09",
    status: "Under review",
  },
  {
    id: "shadow-003",
    appName: "Gemini",
    appDomain: "gemini.google.com",
    appCategory: "Generative AI",
    riskScore: 5,
    detectedUsers: 3,
    firstSeen: "2026-04-22",
    lastSeen: "2026-05-08",
    status: "Detected",
  },
  {
    id: "shadow-004",
    appName: "Perplexity",
    appDomain: "perplexity.ai",
    appCategory: "AI Research",
    riskScore: 4,
    detectedUsers: 2,
    firstSeen: "2026-04-28",
    lastSeen: "2026-05-07",
    status: "Detected",
  },
  {
    id: "shadow-005",
    appName: "Character.AI",
    appDomain: "character.ai",
    appCategory: "Generative AI",
    riskScore: 8,
    detectedUsers: 1,
    firstSeen: "2026-05-01",
    lastSeen: "2026-05-06",
    status: "Blocked",
  },
  {
    id: "shadow-006",
    appName: "Hugging Face Spaces",
    appDomain: "huggingface.co",
    appCategory: "ML Hosting",
    riskScore: 6,
    detectedUsers: 1,
    firstSeen: "2026-05-04",
    lastSeen: "2026-05-06",
    status: "Under review",
  },
]

// ─── §3.8 Owners — AI use-case owner roster ──────────────────────────

export interface OwnerProfile {
  id: string
  name: string
  email: string
  department: string
  jobTitle: string
  ownedUseCaseCount: number
  recentViolationCount: number
  trainingCompleteAt: string | null
  riskOwnerScore: number // 0-100, lower = better
}

export const OWNERS: OwnerProfile[] = [
  {
    id: "owner-001",
    name: "L. Park [DEMO]",
    email: "l.park@samplefoundation.example",
    department: "Grants Management",
    jobTitle: "Senior Officer",
    ownedUseCaseCount: 3,
    recentViolationCount: 1,
    trainingCompleteAt: "2026-03-18",
    riskOwnerScore: 22,
  },
  {
    id: "owner-002",
    name: "M. Hayes [DEMO]",
    email: "m.hayes@samplefoundation.example",
    department: "Program Officer — Housing",
    jobTitle: "Program Officer",
    ownedUseCaseCount: 2,
    recentViolationCount: 0,
    trainingCompleteAt: "2026-04-02",
    riskOwnerScore: 12,
  },
  {
    id: "owner-003",
    name: "S. Diallo [DEMO]",
    email: "s.diallo@samplefoundation.example",
    department: "Finance",
    jobTitle: "Director, Finance",
    ownedUseCaseCount: 1,
    recentViolationCount: 2,
    trainingCompleteAt: null,
    riskOwnerScore: 48,
  },
  {
    id: "owner-004",
    name: "R. Cohen [DEMO]",
    email: "r.cohen@samplefoundation.example",
    department: "Communications",
    jobTitle: "Comms Manager",
    ownedUseCaseCount: 1,
    recentViolationCount: 0,
    trainingCompleteAt: "2026-03-29",
    riskOwnerScore: 10,
  },
  {
    id: "owner-005",
    name: "V. Hassan [DEMO]",
    email: "v.hassan@samplefoundation.example",
    department: "IT",
    jobTitle: "IT Lead",
    ownedUseCaseCount: 1,
    recentViolationCount: 0,
    trainingCompleteAt: "2026-04-15",
    riskOwnerScore: 8,
  },
  {
    id: "owner-006",
    name: "G. Marsh [DEMO]",
    email: "g.marsh@samplefoundation.example",
    department: "Operations / Admin",
    jobTitle: "Operations Manager",
    ownedUseCaseCount: 1,
    recentViolationCount: 1,
    trainingCompleteAt: "2026-03-22",
    riskOwnerScore: 28,
  },
]

// ─── §3.9 Model Risk — per-model risk profile ────────────────────────

export interface ModelRiskProfile {
  id: string
  modelName: string
  vendor: string
  hostedOn: string
  hallucinationScore: number // 0-100, lower = safer
  biasScore: number
  toxicityScore: number
  privacyScore: number
  securityScore: number
  complianceScore: number
  inUseCases: number
  notes: string
}

export const MODEL_RISK_PROFILES: ModelRiskProfile[] = [
  {
    id: "model-001",
    modelName: "GPT-4o",
    vendor: "OpenAI",
    hostedOn: "Microsoft Copilot",
    hallucinationScore: 18,
    biasScore: 22,
    toxicityScore: 12,
    privacyScore: 8,
    securityScore: 10,
    complianceScore: 6,
    inUseCases: 4,
    notes:
      "Frontier general model. Used by 70%+ of foundation staff via Copilot.",
  },
  {
    id: "model-002",
    modelName: "Claude 3.5 Sonnet",
    vendor: "Anthropic",
    hostedOn: "Anthropic direct",
    hallucinationScore: 14,
    biasScore: 19,
    toxicityScore: 8,
    privacyScore: 9,
    securityScore: 12,
    complianceScore: 8,
    inUseCases: 1,
    notes:
      "Used by IT for help-desk transcript triage. Lower hallucination on reasoning tasks.",
  },
  {
    id: "model-003",
    modelName: "Gemini 1.5 Pro",
    vendor: "Google",
    hostedOn: "Google Workspace",
    hallucinationScore: 22,
    biasScore: 24,
    toxicityScore: 14,
    privacyScore: 12,
    securityScore: 14,
    complianceScore: 11,
    inUseCases: 0,
    notes:
      "Detected in shadow use (3 users). No formal use case yet — pending /register flow.",
  },
  {
    id: "model-004",
    modelName: "GPT-3.5 Turbo",
    vendor: "OpenAI",
    hostedOn: "ChatGPT (web)",
    hallucinationScore: 32,
    biasScore: 28,
    toxicityScore: 18,
    privacyScore: 24,
    securityScore: 26,
    complianceScore: 22,
    inUseCases: 2,
    notes:
      "Legacy; recommend migrating these use cases to GPT-4o via Copilot.",
  },
]

// ─── §3.5 Use-Case Map — graph nodes + edges ─────────────────────────

export type UseCaseMapNodeKind =
  | "use-case"
  | "model"
  | "vendor"
  | "owner"
  | "risk"

export interface UseCaseMapNode {
  id: string
  label: string
  kind: UseCaseMapNodeKind
  // Only carried by "use-case" nodes — drives the map's department and
  // severity filters. Other kinds inherit visibility by adjacency to a
  // matching use-case node (see lib/aispm/use-case-map.ts).
  department?: string
  riskTier?: UseCaseRiskTier
  // Freeform extra fields surfaced in the click-to-open detail panel
  // (e.g. tool, status, owner) — kept loose so both curated and
  // server-derived nodes can populate whatever they have.
  meta?: Record<string, string>
}

export interface UseCaseMapEdge {
  from: string
  to: string
  rel: string // "uses-model" | "owned-by" | "has-risk" | "vendor-of"
}

export const USE_CASE_MAP_NODES: UseCaseMapNode[] = [
  {
    id: "uc-001",
    label: "Grant memo drafting",
    kind: "use-case",
    department: "Grants Management",
    riskTier: "Low",
    meta: { tool: "Microsoft Copilot", status: "Active", owner: "L. Park" },
  },
  {
    id: "uc-002",
    label: "Beneficiary intake summarisation",
    kind: "use-case",
    department: "Program Officer — Housing",
    riskTier: "Medium",
    meta: { tool: "Microsoft Copilot", status: "Active", owner: "M. Hayes" },
  },
  {
    id: "uc-003",
    label: "Press release first-draft",
    kind: "use-case",
    department: "Communications",
    riskTier: "Low",
    meta: { tool: "Microsoft Copilot", status: "Active", owner: "R. Cohen" },
  },
  {
    id: "uc-004",
    label: "990-PF narrative assist",
    kind: "use-case",
    department: "Finance",
    riskTier: "Medium",
    meta: { tool: "Microsoft Copilot", status: "Review", owner: "S. Diallo" },
  },
  {
    id: "uc-005",
    label: "Vendor analysis",
    kind: "use-case",
    department: "Operations / Admin",
    riskTier: "Medium",
    meta: { tool: "ChatGPT", status: "Review", owner: "G. Marsh" },
  },
  {
    id: "uc-006",
    label: "Help-desk transcript triage",
    kind: "use-case",
    department: "IT",
    riskTier: "Low",
    meta: { tool: "Claude", status: "Draft", owner: "V. Hassan" },
  },

  { id: "model-001", label: "GPT-4o", kind: "model" },
  { id: "model-002", label: "Claude 3.5 Sonnet", kind: "model" },
  { id: "model-004", label: "GPT-3.5 Turbo", kind: "model" },

  { id: "vendor-openai", label: "OpenAI", kind: "vendor" },
  { id: "vendor-anthropic", label: "Anthropic", kind: "vendor" },

  { id: "owner-001", label: "L. Park", kind: "owner" },
  { id: "owner-002", label: "M. Hayes", kind: "owner" },
  { id: "owner-003", label: "S. Diallo", kind: "owner" },
  { id: "owner-004", label: "R. Cohen", kind: "owner" },
  { id: "owner-005", label: "V. Hassan", kind: "owner" },
  { id: "owner-006", label: "G. Marsh", kind: "owner" },

  { id: "risk-pii", label: "PII exposure", kind: "risk" },
  { id: "risk-bias", label: "Algorithmic bias", kind: "risk" },
  { id: "risk-hallucination", label: "Hallucination", kind: "risk" },
]

export const USE_CASE_MAP_EDGES: UseCaseMapEdge[] = [
  { from: "uc-001", to: "model-001", rel: "uses-model" },
  { from: "uc-001", to: "owner-001", rel: "owned-by" },
  { from: "uc-001", to: "risk-hallucination", rel: "has-risk" },

  { from: "uc-002", to: "model-001", rel: "uses-model" },
  { from: "uc-002", to: "owner-002", rel: "owned-by" },
  { from: "uc-002", to: "risk-pii", rel: "has-risk" },

  { from: "uc-003", to: "model-001", rel: "uses-model" },
  { from: "uc-003", to: "owner-004", rel: "owned-by" },

  { from: "uc-004", to: "model-001", rel: "uses-model" },
  { from: "uc-004", to: "owner-003", rel: "owned-by" },
  { from: "uc-004", to: "risk-pii", rel: "has-risk" },

  { from: "uc-005", to: "model-004", rel: "uses-model" },
  { from: "uc-005", to: "owner-006", rel: "owned-by" },
  { from: "uc-005", to: "risk-hallucination", rel: "has-risk" },

  { from: "uc-006", to: "model-002", rel: "uses-model" },
  { from: "uc-006", to: "owner-005", rel: "owned-by" },

  { from: "model-001", to: "vendor-openai", rel: "vendor-of" },
  { from: "model-004", to: "vendor-openai", rel: "vendor-of" },
  { from: "model-002", to: "vendor-anthropic", rel: "vendor-of" },
]

// ─── Endpoint compliance (see docs/mdm-strategy.md §4.4) ─────────────
//
// Per-MDM aggregate view used by /dashboard/endpoints. Once each MDM
// has a live sync worker, replace with `api.listEndpointCompliance()`.
// `extensionInstalledCount` comes from the Promptly extension's
// device-register heartbeat — for the demo it's slightly less than
// `enrolledCount` to surface the gap the dashboard is built to close.

export interface EndpointMdmAggregate {
  mdmProvider: string // "MICROSOFT_INTUNE" | "JAMF_PRO" | "KANDJI" | "JUMPCLOUD"
  displayName: string
  vendor: "microsoft" | "other"
  enrolledCount: number
  compliantCount: number
  extensionInstalledCount: number
  lastSyncedAt: string | null
}

export const ENDPOINT_MDM_AGGREGATES: EndpointMdmAggregate[] = [
  {
    mdmProvider: "MICROSOFT_INTUNE",
    displayName: "Microsoft Intune",
    vendor: "microsoft",
    enrolledCount: 78,
    compliantCount: 76,
    extensionInstalledCount: 71,
    lastSyncedAt: "2026-05-12T08:00:00Z",
  },
  {
    mdmProvider: "JAMF_PRO",
    displayName: "Jamf Pro",
    vendor: "other",
    // Demo: a small Mac fleet covered by Jamf alongside the Intune
    // Windows fleet. Drops to 0/0/0 if Jamf isn't connected yet.
    enrolledCount: 4,
    compliantCount: 4,
    extensionInstalledCount: 3,
    lastSyncedAt: "2026-05-12T07:55:00Z",
  },
  {
    mdmProvider: "KANDJI",
    displayName: "Kandji",
    vendor: "other",
    enrolledCount: 0,
    compliantCount: 0,
    extensionInstalledCount: 0,
    lastSyncedAt: null,
  },
  {
    mdmProvider: "JUMPCLOUD",
    displayName: "JumpCloud",
    vendor: "other",
    enrolledCount: 0,
    compliantCount: 0,
    extensionInstalledCount: 0,
    lastSyncedAt: null,
  },
]

// Per-device sample rows — what the /dashboard/endpoints detail table
// shows when an admin drills into Intune. v0.2 swaps for
// api.listIntuneDevices().

export interface EndpointDevice {
  id: string
  mdmProvider: string
  deviceName: string
  operatingSystem: string
  osVersion: string
  enrolledUserEmail: string
  complianceState: "compliant" | "noncompliant" | "inGracePeriod" | "unknown"
  extensionInstalled: boolean
  lastSeenAt: string // ISO
}

export const ENDPOINT_DEVICES: EndpointDevice[] = [
  {
    id: "dev-001",
    mdmProvider: "MICROSOFT_INTUNE",
    deviceName: "LAPTOP-LP-SURFACE-01",
    operatingSystem: "Windows",
    osVersion: "11.0.22631",
    enrolledUserEmail: "l.park@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: true,
    lastSeenAt: "2026-05-12T07:42:00Z",
  },
  {
    id: "dev-002",
    mdmProvider: "MICROSOFT_INTUNE",
    deviceName: "LAPTOP-MH-SURFACE-02",
    operatingSystem: "Windows",
    osVersion: "11.0.22631",
    enrolledUserEmail: "m.hayes@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: true,
    lastSeenAt: "2026-05-12T07:38:00Z",
  },
  {
    id: "dev-003",
    mdmProvider: "MICROSOFT_INTUNE",
    deviceName: "MBP-VH-IT-01",
    operatingSystem: "macOS",
    osVersion: "14.5",
    enrolledUserEmail: "v.hassan@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: true,
    lastSeenAt: "2026-05-12T06:21:00Z",
  },
  {
    id: "dev-004",
    mdmProvider: "MICROSOFT_INTUNE",
    deviceName: "LAPTOP-SD-SURFACE-03",
    operatingSystem: "Windows",
    osVersion: "11.0.22631",
    enrolledUserEmail: "s.diallo@samplefoundation.example",
    complianceState: "noncompliant",
    extensionInstalled: false,
    lastSeenAt: "2026-05-12T05:14:00Z",
  },
  {
    id: "dev-005",
    mdmProvider: "MICROSOFT_INTUNE",
    deviceName: "LAPTOP-RC-SURFACE-04",
    operatingSystem: "Windows",
    osVersion: "11.0.22631",
    enrolledUserEmail: "r.cohen@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: false,
    lastSeenAt: "2026-05-11T18:02:00Z",
  },
  {
    id: "dev-006",
    mdmProvider: "MICROSOFT_INTUNE",
    deviceName: "LAPTOP-BT-OFFBOARD",
    operatingSystem: "Windows",
    osVersion: "11.0.22000",
    enrolledUserEmail: "b.tanaka@samplefoundation.example",
    complianceState: "inGracePeriod",
    extensionInstalled: true,
    lastSeenAt: "2026-05-05T13:09:00Z",
  },
  // ─── Jamf-managed Macs ─────────────────────────────────────────
  {
    id: "dev-007",
    mdmProvider: "JAMF_PRO",
    deviceName: "MBP-MARKETING-01",
    operatingSystem: "macOS",
    osVersion: "14.5",
    enrolledUserEmail: "r.cohen@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: true,
    lastSeenAt: "2026-05-12T07:48:00Z",
  },
  {
    id: "dev-008",
    mdmProvider: "JAMF_PRO",
    deviceName: "MBP-EXEC-AW-01",
    operatingSystem: "macOS",
    osVersion: "14.5",
    enrolledUserEmail: "a.weiss@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: true,
    lastSeenAt: "2026-05-12T06:12:00Z",
  },
  {
    id: "dev-009",
    mdmProvider: "JAMF_PRO",
    deviceName: "MBP-DESIGN-01",
    operatingSystem: "macOS",
    osVersion: "14.5",
    enrolledUserEmail: "f.iyer@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: false,
    lastSeenAt: "2026-05-11T22:30:00Z",
  },
  {
    id: "dev-010",
    mdmProvider: "JAMF_PRO",
    deviceName: "MBP-EXEC-EB-01",
    operatingSystem: "macOS",
    osVersion: "14.4",
    enrolledUserEmail: "e.bach@samplefoundation.example",
    complianceState: "compliant",
    extensionInstalled: true,
    lastSeenAt: "2026-05-12T07:01:00Z",
  },
]

// ─── Module-load reconciliation guard ────────────────────────────────
//
// The cheapest enforcement of the rule "dept headcounts must sum to
// ORG.totalUsers". If we ever forget to update one place when adding
// a department, this throws and the page crashes loudly instead of
// silently lying.

const _DEPT_TOTAL = DEPARTMENTS.reduce((s, d) => s + d.users, 0)
if (_DEPT_TOTAL !== ORG.totalUsers) {
  throw new Error(
    `[curated-demo-data] Dept headcount sum (${_DEPT_TOTAL}) does not match ORG.totalUsers (${ORG.totalUsers}). ` +
      `Update DEPARTMENTS or ORG to reconcile.`,
  )
}

// ─── Helpers ─────────────────────────────────────────────────────────

export function eventsByType(type: ActivityEventType): ActivityEvent[] {
  return ACTIVITY_EVENTS.filter((e) => e.eventType === type)
}

export function eventsBySeverity(severity: ActivitySeverity): ActivityEvent[] {
  return ACTIVITY_EVENTS.filter((e) => e.severity === severity)
}

export function recentRedactions(limit = 5): ActivityEvent[] {
  return ACTIVITY_EVENTS.filter(
    (e) => e.eventType === "Redacted" || e.eventType === "Anonymised",
  ).slice(0, limit)
}

/// Cross-page rule: the "412 redacted" number on Overview /
/// Coaching / Audit must match. Use this helper everywhere that
/// number renders.
export function totalRedactedLabel(): string {
  return COUNTS_30D.redactedPrompts.toLocaleString()
}

export function activeUserShare(): string {
  return `${ORG.activeUsers} / ${ORG.totalUsers}`
}

export function activeUserPct(): number {
  return Math.round((ORG.activeUsers / ORG.totalUsers) * 1000) / 10
}
