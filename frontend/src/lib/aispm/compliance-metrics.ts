// Single source of truth for /comply percentages — derived from USE_CASES;
// never hard-code coverage numbers in pages.
//
// Single source of truth for framework compliance coverage.
//
// Every coverage number on the dashboard (the /comply framework cards, the
// coverage-breakdown bar, the ISO 42001 journey baseline, and the board
// report) is derived here from the one USE_CASES registry — so the figures
// can never drift apart on screen.
//
// Coverage % is defined as covered / total (the green segment of the
// breakdown bar); gapCount is the number of use cases with an open gap.
//
// EU AI Act / NIST AI RMF / OWASP LLM / ISO 42001 are stored per use case
// (complianceStatus). GDPR and EIOPA are *derived* from registry signals
// (data classification, data-leakage risk + mitigations, ownership and
// status) so we don't hand-maintain two more columns across every use case
// and the privacy posture stays tied to the underlying data risk.

import { USE_CASES } from '@/lib/aispm/use-cases'
import type { ComplianceLevel, ComplianceStatus, UseCase } from '@/lib/aispm/use-cases'

/** Stored frameworks + the two derived ones (GDPR, EIOPA). */
export type FrameworkKey = keyof ComplianceStatus | 'gdpr' | 'eiopa'

export interface FrameworkMetrics {
  key: FrameworkKey
  name: string
  color: string
  covered: number
  partial: number
  gap: number
  total: number
  /** Headline coverage %, defined as covered / total. */
  percentage: number
  /** Use cases with an open gap for this framework. */
  gapCount: number
}

// ── Derived frameworks ──────────────────────────────────────────────────

/**
 * GDPR posture for a use case, derived from data sensitivity and whether the
 * data-leakage risk is mitigated. Sensitive data + an unmitigated leakage
 * risk is a gap; any sensitive data or data risk is partial; otherwise
 * covered. Privacy is Gjensidige's top concern, so this drives the lead view.
 */
function gdprLevel(uc: UseCase): ComplianceLevel {
  const sensitive =
    uc.dataClassification === 'confidential' || uc.dataClassification === 'restricted'
  const dataRisks = uc.risks.filter((r) => r.category === 'data-leakage')
  const hasUnmitigatedDataRisk = dataRisks.some(
    (r) => !r.mitigations.some((m) => m.status === 'applied'),
  )
  if (sensitive && hasUnmitigatedDataRisk) return 'gap'
  if (sensitive || dataRisks.length > 0) return 'partial'
  return 'covered'
}

/**
 * EIOPA (insurance AI governance) posture, derived from accountability and
 * lifecycle status. No owner = governance gap; mitigated/compliant = covered;
 * owned/assessed = partial; discovered-only = gap.
 */
function eiopaLevel(uc: UseCase): ComplianceLevel {
  if (uc.ownerId === null) return 'gap'
  if (uc.status === 'compliant' || uc.status === 'mitigated') return 'covered'
  if (uc.status === 'owned' || uc.status === 'assessed') return 'partial'
  return 'gap'
}

/** The compliance level for any framework — stored or derived. */
export function frameworkLevel(uc: UseCase, key: FrameworkKey): ComplianceLevel {
  if (key === 'gdpr') return gdprLevel(uc)
  if (key === 'eiopa') return eiopaLevel(uc)
  return uc.complianceStatus[key]
}

// ── Framework definitions (GDPR + EIOPA lead, per Gjensidige priority) ───

const FRAMEWORK_DEFS: { key: FrameworkKey; name: string; color: string }[] = [
  { key: 'gdpr', name: 'GDPR', color: '#6366f1' },
  { key: 'eiopa', name: 'EIOPA', color: '#14b8a6' },
  { key: 'euAiAct', name: 'EU AI Act', color: '#22c55e' },
  { key: 'nistAiRmf', name: 'NIST AI RMF 2.0', color: '#0ea5e9' },
  { key: 'owaspLlm', name: 'OWASP LLM Top 10', color: '#f59e0b' },
  { key: 'iso42001', name: 'ISO 42001', color: '#8b5cf6' },
]

function compute(key: FrameworkKey, name: string, color: string): FrameworkMetrics {
  const total = USE_CASES.length
  let covered = 0
  let partial = 0
  for (const uc of USE_CASES) {
    const level = frameworkLevel(uc, key)
    if (level === 'covered') covered++
    else if (level === 'partial') partial++
  }
  const gap = total - covered - partial
  return {
    key,
    name,
    color,
    covered,
    partial,
    gap,
    total,
    percentage: total > 0 ? Math.round((covered / total) * 100) : 0,
    gapCount: gap,
  }
}

export const FRAMEWORK_METRICS: FrameworkMetrics[] = FRAMEWORK_DEFS.map((d) =>
  compute(d.key, d.name, d.color),
)

export function getFrameworkMetrics(key: FrameworkKey): FrameworkMetrics {
  const m = FRAMEWORK_METRICS.find((f) => f.key === key)
  if (!m) throw new Error(`Unknown framework key: ${key}`)
  return m
}

/** Total use cases in the registry — the single count discovery should report. */
export const USE_CASE_COUNT = USE_CASES.length

/** Average headline coverage across all frameworks. */
export const AVERAGE_COVERAGE = Math.round(
  FRAMEWORK_METRICS.reduce((sum, f) => sum + f.percentage, 0) / FRAMEWORK_METRICS.length,
)
