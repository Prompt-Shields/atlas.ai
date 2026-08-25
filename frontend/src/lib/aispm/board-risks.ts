// Board-ready AI risk roll-up.
//
// Turns the USE_CASES registry + derived compliance metrics into the short,
// ranked list of top risks a Head of AI Risk can report to the board. Every
// figure is computed from the same single source of truth as the rest of the
// dashboard (see compliance-metrics.ts), so the board pack never contradicts
// the operational views.
//
// Ranking is weighted by a risk-appetite profile. The default is "privacy-led"
// (Gjensidige): fines that bite hardest — GDPR (privacy) and AML (financial
// crime) — are weighted highest; operational/availability and ESG are weighted
// down, matching an insurer that is privacy-first and can tolerate downtime.

import { USE_CASES } from '@/lib/aispm/use-cases'
import type { Risk, UseCase } from '@/lib/aispm/use-cases'
import { getFrameworkMetrics, AVERAGE_COVERAGE } from '@/lib/aispm/compliance-metrics'

export type Severity = 'critical' | 'high' | 'medium' | 'low'

const SEVERITY_WEIGHT: Record<Severity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
}

// ── Risk-appetite profile (drives ranking) ──────────────────────────────

export type RiskDimension =
  | 'privacy'
  | 'financial-crime'
  | 'regulatory'
  | 'security'
  | 'operational'
  | 'esg'

export const RISK_APPETITE_PROFILE = 'Privacy-led'

/** Multipliers reflecting where the largest fines / board concern sit. */
export const RISK_APPETITE: Record<RiskDimension, number> = {
  privacy: 2.0,
  'financial-crime': 1.6,
  regulatory: 1.3,
  security: 1.0,
  operational: 0.5,
  esg: 0.2,
}

export interface BoardRisk {
  id: string
  rank: number
  title: string
  severity: Severity
  dimension: RiskDimension
  /** The single number the board sees (e.g. 12). */
  metric: number
  /** Unit/label for the metric (e.g. "unsanctioned tools"). */
  unit: string
  detail: string
  /** Frameworks this risk bears on. */
  frameworks: string[]
  /** What's being done / recommended. */
  remediation: string
  /** Regulatory or internal deadline, if any. */
  deadline?: string
  /** Direction vs last quarter (illustrative). */
  trend: 'up' | 'down' | 'flat'
}

// ── Derived counts (single source of truth: USE_CASES) ──────────────────

const hasSeverity = (uc: UseCase, sevs: Severity[]) =>
  uc.risks.some((r: Risk) => sevs.includes(r.severity as Severity))

const SHADOW_AI = USE_CASES.filter((uc) => uc.discoveryMethod === 'shadow-ai')

const HIGH_RISK = USE_CASES.filter((uc) => hasSeverity(uc, ['critical', 'high']))

const UNOWNED_HIGH_RISK = HIGH_RISK.filter((uc) => uc.ownerId === null)

const SENSITIVE_LEAKAGE = USE_CASES.filter(
  (uc) =>
    (uc.dataClassification === 'confidential' || uc.dataClassification === 'restricted') &&
    uc.risks.some((r) => r.category === 'data-leakage'),
)

const PROMPT_INJECTION = USE_CASES.filter((uc) =>
  uc.risks.some((r) => r.category === 'prompt-injection'),
)

const gdpr = getFrameworkMetrics('gdpr')
const euAiAct = getFrameworkMetrics('euAiAct')
const iso42001 = getFrameworkMetrics('iso42001')

export const BOARD_COUNTS = {
  totalUseCases: USE_CASES.length,
  highRisk: HIGH_RISK.length,
  unownedHighRisk: UNOWNED_HIGH_RISK.length,
  shadowAi: SHADOW_AI.length,
  sensitiveLeakage: SENSITIVE_LEAKAGE.length,
  promptInjection: PROMPT_INJECTION.length,
  gdprGaps: gdpr.gapCount,
  averageCoverage: AVERAGE_COVERAGE,
}

// ── Top risks (ranked by severity × exposure × risk-appetite weight) ────

const RAW_RISKS: Omit<BoardRisk, 'rank'>[] = [
  {
    id: 'gdpr-exposure',
    title: 'GDPR / privacy exposure on personal data',
    severity: 'critical',
    dimension: 'privacy',
    // Personal-data use cases not yet fully covered (partial + gap) — the
    // board-level privacy exposure, not just the strict-gap subset.
    metric: gdpr.partial + gdpr.gap,
    unit: 'use cases handling personal data at risk',
    detail: `${gdpr.partial + gdpr.gap} use cases process personal data without full controls (${gdpr.gapCount} with open gaps). Privacy carries the largest fine exposure — up to 4% of global turnover — and is the board’s primary concern.`,
    frameworks: ['GDPR', 'EU AI Act'],
    remediation: 'Enforce PII redaction + lawful-basis checks; close gaps on restricted data first.',
    trend: 'down',
  },
  {
    id: 'shadow-ai',
    title: 'Shadow AI processing company data unreviewed',
    severity: 'critical',
    dimension: 'privacy',
    metric: SHADOW_AI.length,
    unit: 'unsanctioned tools',
    detail:
      'AI tools adopted without review, several handling confidential data. No owner, no risk assessment, no policy applied — a direct privacy and data-residency exposure.',
    frameworks: ['GDPR', 'EU AI Act', 'ISO 42001'],
    remediation: 'Triage in registry → assign owners → apply guardrails or sunset.',
    trend: 'down',
  },
  {
    id: 'sensitive-leakage',
    title: 'Sensitive data exposure to external LLMs',
    severity: 'high',
    dimension: 'privacy',
    metric: SENSITIVE_LEAKAGE.length,
    unit: 'use cases',
    detail:
      'Use cases handling confidential or restricted data with an open data-leakage risk — PII/IP can leave the boundary, including to external API providers.',
    frameworks: ['GDPR', 'OWASP LLM Top 10'],
    remediation: 'Enforce PII redaction + data-minimisation before send.',
    trend: 'down',
  },
  {
    id: 'unowned-high-risk',
    title: 'High-risk AI with no accountable owner',
    severity: 'high',
    dimension: 'regulatory',
    metric: UNOWNED_HIGH_RISK.length,
    unit: 'use cases',
    detail:
      'High-risk use cases carrying critical or high-severity risks with no named owner — direct regulatory and audit liability under EIOPA and the EU AI Act.',
    frameworks: ['EIOPA', 'EU AI Act', 'NIST AI RMF'],
    remediation: 'Assign owners and require sign-off within the quarter.',
    trend: 'flat',
  },
  {
    id: 'eu-ai-act-gaps',
    title: 'EU AI Act obligations not yet met',
    severity: 'high',
    dimension: 'regulatory',
    metric: euAiAct.gapCount,
    unit: 'open gaps',
    detail: `${euAiAct.gapCount} use cases with EU AI Act gaps against the high-risk obligations coming into force.`,
    frameworks: ['EU AI Act'],
    remediation: 'Close gaps prioritised by deadline; document technical evidence.',
    deadline: 'Aug 2026',
    trend: 'down',
  },
  {
    id: 'prompt-injection',
    title: 'Prompt-injection exposure on LLM use cases',
    severity: 'medium',
    dimension: 'security',
    metric: PROMPT_INJECTION.length,
    unit: 'use cases',
    detail:
      'Customer- or data-facing LLM use cases exposed to prompt-injection without consistent input/output guards.',
    frameworks: ['OWASP LLM Top 10', 'NIST AI RMF'],
    remediation: 'Apply prompt-injection guards; wire into red-team CI.',
    trend: 'flat',
  },
  {
    id: 'iso-42001-readiness',
    title: 'ISO/IEC 42001 management-system gaps',
    severity: 'medium',
    dimension: 'regulatory',
    metric: iso42001.gapCount,
    unit: 'open gaps',
    detail: `Currently ${iso42001.percentage}% coverage. Tracked to show governance progression — not a certification target for now.`,
    frameworks: ['ISO 42001'],
    remediation: 'Progress the guided ISO 42001 journey to show maturity to the board.',
    trend: 'down',
  },
]

function score(r: Omit<BoardRisk, 'rank'>): number {
  return SEVERITY_WEIGHT[r.severity] * Math.max(r.metric, 1) * RISK_APPETITE[r.dimension]
}

/** Top risks, ranked by severity × exposure × risk-appetite weight. */
export const TOP_RISKS: BoardRisk[] = [...RAW_RISKS]
  .sort((a, b) => score(b) - score(a))
  .map((r, i) => ({ ...r, rank: i + 1 }))

// ── Overall posture ─────────────────────────────────────────────────────

export type Posture = 'Low' | 'Moderate' | 'Elevated' | 'High'

/**
 * A single posture label derived from open privacy-critical exposure and
 * average coverage. Demo heuristic, but deterministic and explainable to a
 * board — and weighted to privacy, matching the risk-appetite profile.
 */
export function overallPosture(): { label: Posture; trend: 'improving' | 'stable' | 'worsening' } {
  const privacyCriticalOpen = TOP_RISKS.filter(
    (r) => r.dimension === 'privacy' && r.severity === 'critical' && r.metric > 0,
  ).length
  const cov = AVERAGE_COVERAGE
  let label: Posture
  if (privacyCriticalOpen >= 2) label = 'High'
  else if (privacyCriticalOpen === 1 || cov < 50) label = 'Elevated'
  else if (cov < 70) label = 'Moderate'
  else label = 'Low'
  return { label, trend: 'improving' }
}
