// ISO/IEC 42001 compliance journey — demo data + progress state.
//
// Drives the step-by-step admin guide at /comply/iso-42001. The journey
// walks the seven management-system clauses (4–10) of ISO/IEC 42001:2023;
// completing each step theatrically raises the framework's coverage from
// a starting 55% toward a "certification-ready" 100%.
//
// Progress is illustrative and self-contained — it persists to
// localStorage (no backend) so the /comply page can reflect the climbed
// score after the journey is run. Reset restores the 55% baseline.

import { getFrameworkMetrics } from '@/lib/aispm/compliance-metrics'

// Baseline coverage/gaps are derived from the shared compliance metrics
// (the same source the /comply ISO 42001 card uses), so the journey starts
// at exactly the number shown on the overview — no drift.
const ISO_METRICS = getFrameworkMetrics('iso42001')

export const ISO42001_BASE_COVERAGE = ISO_METRICS.percentage
export const ISO42001_TARGET_COVERAGE = 100
export const ISO42001_BASE_GAPS = ISO_METRICS.gapCount
export const ISO42001_COLOR = ISO_METRICS.color
export const ISO42001_STORAGE_KEY = 'aispm.iso42001.journey'

/** An Annex A control reference satisfied by a clause step. */
export interface AnnexControl {
  id: string
  name: string
}

/** One step of the journey — a single ISO 42001 management clause. */
export interface Iso42001Step {
  id: string
  clause: string
  clauseTitle: string
  /** What the admin does in this step. */
  action: string
  /** The evidence/artifact the step produces. */
  artifact: string
  /** Annex A controls this step contributes to. */
  annexControls: AnnexControl[]
}

export const ISO42001_STEPS: Iso42001Step[] = [
  {
    id: 'clause-4',
    clause: 'Clause 4',
    clauseTitle: 'Context of the organization',
    action:
      'Define the scope of your AI management system (AIMS) and identify the internal/external issues and interested parties relevant to your AI use.',
    artifact: 'AIMS Scope Statement & Interested-Parties Register',
    annexControls: [
      { id: 'A.3', name: 'Internal organization' },
      { id: 'A.8', name: 'Information for interested parties' },
    ],
  },
  {
    id: 'clause-5',
    clause: 'Clause 5',
    clauseTitle: 'Leadership',
    action:
      'Publish the organizational AI policy, secure leadership commitment, and assign AI roles, responsibilities and accountabilities (RACI).',
    artifact: 'AI Policy & Roles/Accountability (RACI) matrix',
    annexControls: [
      { id: 'A.2', name: 'Policies related to AI' },
      { id: 'A.3', name: 'Internal organization' },
    ],
  },
  {
    id: 'clause-6',
    clause: 'Clause 6',
    clauseTitle: 'Planning',
    action:
      'Run AI risk assessments and AI system impact assessments across your inventory, and set measurable AI objectives to address risks and opportunities.',
    artifact: 'AI Risk Register & AI System Impact Assessments',
    annexControls: [{ id: 'A.5', name: 'Assessing impacts of AI systems' }],
  },
  {
    id: 'clause-7',
    clause: 'Clause 7',
    clauseTitle: 'Support',
    action:
      'Provision resources, establish competence and awareness for staff working with AI, and bring documented information under version control.',
    artifact: 'Competence & Awareness records · Documented-Information index',
    annexControls: [
      { id: 'A.4', name: 'Resources for AI systems' },
      { id: 'A.7', name: 'Data for AI systems' },
    ],
  },
  {
    id: 'clause-8',
    clause: 'Clause 8',
    clauseTitle: 'Operation',
    action:
      'Implement operational controls, execute the risk treatment plan against open gaps, and record a Statement of Applicability over the Annex A controls.',
    artifact: 'Risk Treatment Plan & Statement of Applicability (SoA)',
    annexControls: [
      { id: 'A.6', name: 'AI system life cycle' },
      { id: 'A.9', name: 'Use of AI systems' },
      { id: 'A.10', name: 'Third-party relationships' },
    ],
  },
  {
    id: 'clause-9',
    clause: 'Clause 9',
    clauseTitle: 'Performance evaluation',
    action:
      'Monitor and measure AIMS performance against your objectives, conduct an internal audit, and hold a management review.',
    artifact: 'Internal Audit Report · Management Review minutes · KPIs',
    annexControls: [{ id: 'A.6', name: 'AI system life cycle' }],
  },
  {
    id: 'clause-10',
    clause: 'Clause 10',
    clauseTitle: 'Improvement',
    action:
      'Log nonconformities, drive corrective actions to closure, and establish a continual-improvement loop for the AIMS.',
    artifact: 'Nonconformity & Corrective-Action log · Improvement plan',
    annexControls: [{ id: 'A.2', name: 'Policies related to AI' }],
  },
]

export const ISO42001_STEP_COUNT = ISO42001_STEPS.length

export interface IsoCensus {
  covered: number
  partial: number
  gap: number
  total: number
  percentage: number
}

/**
 * Census of ISO 42001 use cases at a given journey progress. Starts at the
 * shared baseline (covered/partial/gap from compliance-metrics) and, as
 * clauses complete, converts partial + gap use cases to covered — reaching
 * full coverage when all clauses are done. Coverage % is covered/total, so
 * it matches the /comply breakdown bar exactly.
 */
export function isoCensus(completedCount: number): IsoCensus {
  const total = ISO_METRICS.total
  const ratio = Math.min(completedCount, ISO42001_STEP_COUNT) / ISO42001_STEP_COUNT
  const gap = Math.round(ISO_METRICS.gap * (1 - ratio))
  const partial = Math.round(ISO_METRICS.partial * (1 - ratio))
  const covered = total - gap - partial
  return {
    covered,
    partial,
    gap,
    total,
    percentage: total > 0 ? Math.round((covered / total) * 100) : 0,
  }
}

/** Coverage %, census-based, climbing from the baseline toward 100%. */
export function computeCoverage(completedCount: number): number {
  return isoCensus(completedCount).percentage
}

/** Remaining gaps, shrinking from the baseline to 0 as clauses complete. */
export function computeGapCount(completedCount: number): number {
  return isoCensus(completedCount).gap
}

export function isCertificationReady(completedCount: number): boolean {
  return completedCount >= ISO42001_STEP_COUNT
}

/** Read the completed-step ids from localStorage (SSR-safe). */
export function readCompletedSteps(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(ISO42001_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Keep only ids we recognise, in canonical step order.
    return ISO42001_STEPS.map((s) => s.id).filter((id) => parsed.includes(id))
  } catch {
    return []
  }
}

/** Persist the completed-step ids to localStorage. */
export function writeCompletedSteps(ids: string[]): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(ISO42001_STORAGE_KEY, JSON.stringify(ids))
}
