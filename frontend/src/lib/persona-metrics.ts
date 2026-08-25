// Persona-keyed AI-SPM metrics, ported from the security-platform-demo
// dashboard redesign. Customer feedback was clear: executives, finance,
// security, technology, and compliance leaders each want a focused view
// of *their* four metrics, with the data source for every number
// visible on the card. This module is the single source of truth for
// those 20 metrics.
//
// When the AI-SPM API surface lands (cursor's M2), every PersonaMetric
// gains a `dataLoader` async function that fetches the live value;
// today the values are pinned strings so the overview page renders.
//
// Source: <https://github.com/Bit-Pulse-AI/security-platform-demo/blob/main/DASHBOARD_REDESIGN_PLAN.md>

export type PersonaId =
  | "executive"
  | "finance"
  | "security"
  | "technology"
  | "compliance"

export interface Persona {
  id: PersonaId
  label: string
  short: string
  description: string
  /// Tailwind colour token used for the persona's accent (matches our
  /// existing badge variants).
  accent: "indigo" | "amber" | "rose" | "sky" | "emerald"
}

export const PERSONAS: Persona[] = [
  {
    id: "executive",
    label: "Executive (CEO / Board)",
    short: "Executive",
    description:
      "Strategic alignment, brand risk exposure, talent impact, captured business value.",
    accent: "indigo",
  },
  {
    id: "finance",
    label: "Finance (CFO)",
    short: "Finance",
    description: "Total cost of ownership, ROI, vendor lock-in, shadow AI spend.",
    accent: "amber",
  },
  {
    id: "security",
    label: "Security (CISO)",
    short: "Security",
    description:
      "Data leakage prevention, model integrity, supply chain risk, shadow AI visibility.",
    accent: "rose",
  },
  {
    id: "technology",
    label: "Technology (CTO)",
    short: "Technology",
    description:
      "Integration health, data pipeline readiness, code maintainability, performance SLA.",
    accent: "sky",
  },
  {
    id: "compliance",
    label: "Compliance & Legal (CCO)",
    short: "Compliance",
    description:
      "Regulatory alignment, model explainability, bias & fairness, IP ownership.",
    accent: "emerald",
  },
]

/// A single metric on a persona dashboard.
///
/// Customer feedback from security-platform-demo testing: every metric
/// must show *which system* the number came from. This is the
/// difference between "trust me" and "click through to verify".
export interface PersonaMetric {
  id: string
  personaId: PersonaId
  title: string
  /// The exec-level question this metric answers in plain English.
  questionAnswered: string
  /// Where the value came from. Must be a real system path (Azure ML,
  /// Purview, Compliance Manager, etc.) — NOT a vague "Custom Analytics".
  dataSource: string
  /// Pinned current value. When M2 lands these become async loaders
  /// against the relevant API.
  currentValue: string
  /// Trend direction over the last 30 days. Optional — some metrics
  /// don't trend (e.g. counts).
  trend?: {
    direction: "up" | "down" | "flat"
    deltaPct?: number
    /// True if the direction is GOOD (e.g. "down" is good for risk).
    isPositive: boolean
  }
  /// Where to send the user when they click the card. Should be an
  /// existing dashboard route. Empty string means no drill-down yet.
  drillDownHref: string
  /// Severity tone for the value display. "warn" = amber, "alert" =
  /// red, "ok" = green, "neutral" = no special tint.
  tone: "ok" | "neutral" | "warn" | "alert"
}

export const PERSONA_METRICS: PersonaMetric[] = [
  // ─── Executive ─────────────────────────────────────────────────
  {
    id: "exec-strategic-alignment",
    personaId: "executive",
    title: "AI Strategic Alignment Score",
    questionAnswered: "How aligned is our AI portfolio with corporate strategy?",
    dataSource: "AI Studio + Azure ML",
    currentValue: "78%",
    trend: { direction: "up", deltaPct: 4, isPositive: true },
    drillDownHref: "/dashboard/ai-governance",
    tone: "ok",
  },
  {
    id: "exec-competitive-advantage",
    personaId: "executive",
    title: "Competitive AI Advantage",
    questionAnswered: "What's our AI moat versus peers?",
    dataSource: "Custom Analytics + Use Case Registry",
    currentValue: "62%",
    trend: { direction: "up", deltaPct: 7, isPositive: true },
    drillDownHref: "/dashboard/register",
    tone: "neutral",
  },
  {
    id: "exec-brand-risk",
    personaId: "executive",
    title: "Brand Risk Score",
    questionAnswered: "What's our reputational exposure from AI usage?",
    dataSource: "Purview + Defender Alerts",
    currentValue: "24 incidents (30d)",
    trend: { direction: "down", deltaPct: 18, isPositive: true },
    drillDownHref: "/dashboard/policy-enforcement",
    tone: "warn",
  },
  {
    id: "exec-talent-sentiment",
    personaId: "executive",
    title: "Talent AI Sentiment",
    questionAnswered: "How does AI adoption affect retention and attraction?",
    dataSource: "HR Systems + Employee Survey",
    currentValue: "72% positive",
    trend: { direction: "up", deltaPct: 3, isPositive: true },
    drillDownHref: "",
    tone: "ok",
  },

  // ─── Finance ───────────────────────────────────────────────────
  {
    id: "fin-tco",
    personaId: "finance",
    title: "AI Infrastructure TCO",
    questionAnswered: "What's the all-in annual cost of running our AI?",
    dataSource: "Azure Cost Management",
    currentValue: "$2.4M / year",
    trend: { direction: "up", deltaPct: 12, isPositive: false },
    drillDownHref: "/dashboard/ai-visibility",
    tone: "warn",
  },
  {
    id: "fin-roi",
    personaId: "finance",
    title: "AI ROI Multiple",
    questionAnswered: "How much value are we capturing per dollar spent?",
    dataSource: "Finance Analytics + Use Case Outcomes",
    currentValue: "3.2x",
    trend: { direction: "up", deltaPct: 9, isPositive: true },
    drillDownHref: "/dashboard/register",
    tone: "ok",
  },
  {
    id: "fin-vendor-lockin",
    personaId: "finance",
    title: "Vendor Lock-in Risk Index",
    questionAnswered: "How dependent are we on a single AI vendor?",
    dataSource: "Azure Resource Analysis + Contract Registry",
    currentValue: "45%",
    trend: { direction: "flat", isPositive: true },
    drillDownHref: "/dashboard/ai-visibility",
    tone: "neutral",
  },
  {
    id: "fin-shadow-spend",
    personaId: "finance",
    title: "Shadow AI Spend",
    questionAnswered: "How much are we spending on unsanctioned AI tools?",
    dataSource: "Cost Management + Promptly Detection",
    currentValue: "$340K / year",
    trend: { direction: "up", deltaPct: 22, isPositive: false },
    drillDownHref: "/dashboard/discover",
    tone: "alert",
  },

  // ─── Security (CISO) ───────────────────────────────────────────
  {
    id: "sec-data-leakage",
    personaId: "security",
    title: "Data Leakage Prevention Rate",
    questionAnswered: "Are we blocking PII / PHI / IP from reaching AI?",
    dataSource: "Purview DLP + Promptly Audit Logs",
    currentValue: "99.2% blocked",
    trend: { direction: "up", deltaPct: 0.4, isPositive: true },
    drillDownHref: "/dashboard/policy-enforcement",
    tone: "ok",
  },
  {
    id: "sec-model-integrity",
    personaId: "security",
    title: "Model Integrity Score",
    questionAnswered: "Are deployed models behaving as deployed?",
    dataSource: "Azure ML + Custom Drift Monitoring",
    currentValue: "94%",
    trend: { direction: "down", deltaPct: 1, isPositive: false },
    drillDownHref: "/dashboard/model-risk",
    tone: "warn",
  },
  {
    id: "sec-supply-chain",
    personaId: "security",
    title: "Supply Chain Risk Assessment",
    questionAnswered: "What third-party AI risks are open?",
    dataSource: "Defender Supply Chain + SBOM",
    currentValue: "3 critical risks",
    trend: { direction: "down", deltaPct: 25, isPositive: true },
    drillDownHref: "/dashboard/ai-visibility",
    tone: "alert",
  },
  {
    id: "sec-shadow-detection",
    personaId: "security",
    title: "Shadow AI Detection Count",
    questionAnswered: "How many unsanctioned AI tools are in use?",
    dataSource: "Promptly Endpoint Detection + Browser Telemetry",
    currentValue: "18 detected",
    trend: { direction: "up", deltaPct: 6, isPositive: false },
    drillDownHref: "/dashboard/discover",
    tone: "warn",
  },

  // ─── Technology (CTO) ──────────────────────────────────────────
  {
    id: "tech-integration-health",
    personaId: "technology",
    title: "Integration Health Score",
    questionAnswered: "Are our AI systems integrating cleanly upstream?",
    dataSource: "Azure ML Health + Azure Monitor",
    currentValue: "96% uptime",
    trend: { direction: "flat", isPositive: true },
    drillDownHref: "/dashboard/ai-visibility",
    tone: "ok",
  },
  {
    id: "tech-pipeline-readiness",
    personaId: "technology",
    title: "Data Pipeline Readiness",
    questionAnswered: "Are our pipelines feeding clean data into models?",
    dataSource: "Azure Data Factory + Quality Score",
    currentValue: "87% ready",
    trend: { direction: "up", deltaPct: 5, isPositive: true },
    drillDownHref: "",
    tone: "ok",
  },
  {
    id: "tech-maintainability",
    personaId: "technology",
    title: "Code Maintainability Index",
    questionAnswered: "How sustainable is our AI codebase?",
    dataSource: "Azure DevOps Code Analysis",
    currentValue: "7.8 / 10",
    trend: { direction: "flat", isPositive: true },
    drillDownHref: "",
    tone: "neutral",
  },
  {
    id: "tech-performance-sla",
    personaId: "technology",
    title: "Model Performance SLA",
    questionAnswered: "Are we hitting our latency / accuracy SLAs?",
    dataSource: "Azure ML Metrics + APIM",
    currentValue: "99.1% SLA met",
    trend: { direction: "up", deltaPct: 0.2, isPositive: true },
    drillDownHref: "/dashboard/model-risk",
    tone: "ok",
  },

  // ─── Compliance & Legal (CCO) ──────────────────────────────────
  {
    id: "cmp-regulatory",
    personaId: "compliance",
    title: "Regulatory Compliance Score",
    questionAnswered: "Are we aligned with GDPR / EU AI Act / SOX?",
    dataSource: "Compliance Manager + Audit Logs",
    currentValue: "92% compliant",
    trend: { direction: "up", deltaPct: 2, isPositive: true },
    drillDownHref: "/dashboard/comply",
    tone: "ok",
  },
  {
    id: "cmp-explainability",
    personaId: "compliance",
    title: "Model Explainability Index",
    questionAnswered: "Can we explain a model decision to a regulator?",
    dataSource: "InterpretML + Model Cards",
    currentValue: "8.6 / 10",
    trend: { direction: "up", deltaPct: 0.3, isPositive: true },
    drillDownHref: "/dashboard/model-risk",
    tone: "ok",
  },
  {
    id: "cmp-bias",
    personaId: "compliance",
    title: "Bias & Fairness Audit Score",
    questionAnswered: "Are our models treating protected groups fairly?",
    dataSource: "FairLearn + Custom Audits",
    currentValue: "91% fair",
    trend: { direction: "down", deltaPct: 2, isPositive: false },
    drillDownHref: "/dashboard/ai-governance",
    tone: "warn",
  },
  {
    id: "cmp-ip-ownership",
    personaId: "compliance",
    title: "IP Ownership Clarity",
    questionAnswered: "Do we own the IP for our trained models?",
    dataSource: "Legal Registry + Model Registry",
    currentValue: "100% documented",
    trend: { direction: "flat", isPositive: true },
    drillDownHref: "",
    tone: "ok",
  },
]

export function metricsForPersona(personaId: PersonaId): PersonaMetric[] {
  return PERSONA_METRICS.filter((m) => m.personaId === personaId)
}

// ─── Adoption KPIs (top-of-page strip, persona-agnostic) ─────────────
//
// The four metrics every persona sees regardless of role. Customer
// feedback: "I want one number for whether we're winning at AI" — these
// four are the answer.

export interface AdoptionKpi {
  id: string
  title: string
  subtitle: string
  value: string
  /// 0–100 for the progress bar.
  percentage: number
  accent: "indigo" | "emerald" | "amber" | "violet"
}

export const ADOPTION_KPIS: AdoptionKpi[] = [
  {
    id: "kpi-overall-adoption",
    title: "Overall GenAI Adoption",
    subtitle: "Employees actively using GenAI monthly",
    value: "68%",
    percentage: 68,
    accent: "indigo",
  },
  {
    id: "kpi-trained-workforce",
    title: "Trained Workforce",
    subtitle: "Staff who completed GenAI + AI safety training",
    value: "74%",
    percentage: 74,
    accent: "emerald",
  },
  {
    id: "kpi-business-units-enabled",
    title: "Business Units Enabled",
    subtitle: "Functions with at least one approved AI use case",
    value: "9 / 12",
    percentage: 75,
    accent: "amber",
  },
  {
    id: "kpi-use-cases-prod",
    title: "Use Cases in Production",
    subtitle: "Governed GenAI workflows live in production",
    value: "32",
    percentage: 80,
    accent: "violet",
  },
]
