// Promotion lifecycle: Guideline → Strict (and back).
// Pure, framework-agnostic logic — UI components call these functions.
//
// Rules:
//   1. New instances always start as Guideline regardless of template default.
//      Templates only suggest the *target* mode; admins decide when to flip.
//   2. Promotion to Strict requires ALL of:
//        - N days in Guideline mode (default 14)
//        - FP rate < threshold (default 2%)
//        - Required approvers signed off (per template category)
//   3. Demotion (Strict → Guideline) is always permitted, no approvals.
//   4. Auto-demote watchdog fires when live FP rate exceeds configured cap.

import type {
  PolicyInstance,
  PolicyTemplate,
  PolicyClass,
  PromotionEligibility,
  ApprovalRecord,
  PromotionEvent,
  EnforcementMode,
  AutoDemoteConfig
} from "./policy-types"
import { classOf } from "./policy-types"

// ─── Tunables (could be loaded from admin config) ────────────────────

export const PROMOTION_DEFAULTS = {
  daysInGuidelineRequired: 14,
  fpRateThreshold: 0.02 // 2%
} as const

export const AUTO_DEMOTE_DEFAULTS: AutoDemoteConfig = {
  enabled: true,
  fpRateThreshold: 5, // 5% — much looser than promotion gate; only catches runaway problems
  windowMinutes: 60,
  graceSeconds: 300
}

// ─── Required approvers per template category ────────────────────────
//
// Higher-risk regulatory categories require sign-off from specific roles.
// This is the second gate beyond time-in-mode and FP rate.

const REQUIRED_APPROVERS_BY_CATEGORY: Record<string, string[]> = {
  GDPR: ["DPO"],
  EU_AI_ACT: ["DPO", "Security Lead"],
  INDUSTRY: ["Security Lead", "Compliance Officer"], // PCI / HIPAA / Legal
  OWASP_LLM: ["Security Lead"],
  SHADOW_AI: ["Security Lead"],
  CONTENT_SAFETY: ["Trust & Safety Lead"],
  CUSTOM: ["Policy Author"]
}

export function requiredApproversFor(template: PolicyTemplate): string[] {
  return REQUIRED_APPROVERS_BY_CATEGORY[template.category] ?? ["Security Lead"]
}

// ─── Eligibility check ───────────────────────────────────────────────

export function checkPromotionEligibility(
  instance: PolicyInstance,
  template: PolicyTemplate,
  now: Date = new Date()
): PromotionEligibility {
  const blockingReasons: string[] = []

  // 1. Time in Guideline
  const activatedAt = instance.activatedAt ? new Date(instance.activatedAt) : null
  const daysInGuideline = activatedAt
    ? Math.floor((now.getTime() - activatedAt.getTime()) / 86_400_000)
    : 0
  const daysRequired = PROMOTION_DEFAULTS.daysInGuidelineRequired
  if (daysInGuideline < daysRequired) {
    blockingReasons.push(
      `Need ${daysRequired - daysInGuideline} more day(s) in Guideline mode (currently ${daysInGuideline})`
    )
  }

  // 2. False positive rate
  const fpRate = instance.stats?.falsePositiveRate ?? 0
  const fpThreshold = PROMOTION_DEFAULTS.fpRateThreshold
  if (fpRate > fpThreshold) {
    blockingReasons.push(
      `False-positive rate is ${(fpRate * 100).toFixed(1)}% — must be below ${(fpThreshold * 100).toFixed(1)}%`
    )
  }

  // 3. Approvers
  const required = requiredApproversFor(template)
  const existing = instance.promotionHistory.at(-1)?.approvers ?? []
  const approvers: ApprovalRecord[] = required.map((role) => {
    const found = existing.find((a) => a.role === role)
    return found ?? { role, status: "pending" }
  })
  const missingApprovals = approvers.filter((a) => a.status !== "approved")
  if (missingApprovals.length > 0) {
    blockingReasons.push(
      `Awaiting approval from: ${missingApprovals.map((a) => a.role).join(", ")}`
    )
  }

  return {
    eligible: blockingReasons.length === 0,
    daysInGuideline,
    daysRequired,
    falsePositiveRate: fpRate,
    fpRateThreshold: fpThreshold,
    approvers,
    blockingReasons
  }
}

// ─── Promotion / demotion actions ────────────────────────────────────

export interface PromoteParams {
  instance: PolicyInstance
  template: PolicyTemplate
  by: string
  approvers: ApprovalRecord[]
  rolloutStrategy: "all" | "canary" | "phased"
  rolloutCanaryAppId?: string
  rolloutPercentage?: number
  targetMode?: EnforcementMode // "block" or "redact" — defaults to template suggestion
  now?: Date
}

export interface PromoteResult {
  ok: boolean
  instance?: PolicyInstance
  reason?: string
}

/**
 * Promote a Guideline instance to Strict. Verifies eligibility and writes
 * a PromotionEvent into history. Caller persists via instance-store.
 */
export function promoteToStrict(params: PromoteParams): PromoteResult {
  const { instance, template, by, approvers, rolloutStrategy } = params
  const now = params.now ?? new Date()

  const currentClass = classOf(instance.enforcementMode)
  if (currentClass === "strict") {
    return { ok: false, reason: "Instance is already in Strict mode" }
  }

  // Re-verify eligibility with the supplied approvers list
  const merged: PolicyInstance = {
    ...instance,
    promotionHistory: [
      ...instance.promotionHistory,
      // Stage approvers so eligibility check sees them
      { from: "guideline", to: "strict", at: now.toISOString(), by, approvers }
    ]
  }
  const eligibility = checkPromotionEligibility(merged, template, now)
  if (!eligibility.eligible) {
    return {
      ok: false,
      reason: `Not eligible: ${eligibility.blockingReasons.join("; ")}`
    }
  }

  // Decide target enforcement mode
  const targetMode: EnforcementMode =
    params.targetMode ??
    (template.defaults.enforcementMode === "block" || template.defaults.enforcementMode === "redact"
      ? template.defaults.enforcementMode
      : "block")

  const promotionEvent: PromotionEvent = {
    from: "guideline",
    to: "strict",
    at: now.toISOString(),
    by,
    approvers,
    reason: `Rollout strategy: ${rolloutStrategy}`
  }

  const promoted: PolicyInstance = {
    ...instance,
    enforcementMode: targetMode,
    rolloutStrategy,
    rolloutCanaryAppId: params.rolloutCanaryAppId,
    rolloutPercentage: params.rolloutPercentage,
    promotionHistory: [...instance.promotionHistory, promotionEvent],
    updatedAt: now.toISOString()
  }
  return { ok: true, instance: promoted }
}

export interface DemoteParams {
  instance: PolicyInstance
  by: string
  reason?: string
  now?: Date
}

/**
 * Demote a Strict instance back to Guideline. Always permitted — this is
 * the safety valve. No approval required because *increasing* safety
 * (less enforcement) doesn't need second-signature.
 */
export function demoteToGuideline(params: DemoteParams): PolicyInstance {
  const { instance, by, reason } = params
  const now = params.now ?? new Date()

  const event: PromotionEvent = {
    from: "strict",
    to: "guideline",
    at: now.toISOString(),
    by,
    reason: reason ?? "Manual demotion"
  }

  return {
    ...instance,
    enforcementMode: "log", // Guideline default is log; admin can switch to "flag"
    rolloutStrategy: "all",
    rolloutCanaryAppId: undefined,
    rolloutPercentage: undefined,
    promotionHistory: [...instance.promotionHistory, event],
    updatedAt: now.toISOString()
  }
}

// ─── Auto-demote watchdog ────────────────────────────────────────────

export interface AutoDemoteAssessment {
  shouldDemote: boolean
  reason?: string
  graceUntil?: string // ISO timestamp when grace period ends
}

/**
 * Evaluates current stats against the auto-demote config. Caller is
 * responsible for actually firing demoteToGuideline() after grace period.
 *
 * Typical wiring: a server-side cron runs every minute, calls this for
 * every Strict instance, and tracks "first detected" timestamps for grace
 * period calculation.
 */
export function assessAutoDemote(
  instance: PolicyInstance,
  config: AutoDemoteConfig,
  firstDetectedAt?: Date,
  now: Date = new Date()
): AutoDemoteAssessment {
  if (!config.enabled) return { shouldDemote: false }
  if (classOf(instance.enforcementMode) !== "strict") return { shouldDemote: false }

  const fpRate = (instance.stats?.falsePositiveRate ?? 0) * 100
  if (fpRate <= config.fpRateThreshold) {
    return { shouldDemote: false }
  }

  // FP rate is over the cap. If this is the first detection, start grace.
  if (!firstDetectedAt) {
    return {
      shouldDemote: false,
      reason: `FP rate ${fpRate.toFixed(1)}% > ${config.fpRateThreshold}% — grace period started`,
      graceUntil: new Date(now.getTime() + config.graceSeconds * 1000).toISOString()
    }
  }

  // Grace period elapsed?
  const graceEnds = new Date(firstDetectedAt.getTime() + config.graceSeconds * 1000)
  if (now >= graceEnds) {
    return {
      shouldDemote: true,
      reason: `FP rate ${fpRate.toFixed(1)}% sustained above ${config.fpRateThreshold}% for ${config.graceSeconds}s — auto-demoting`
    }
  }

  return {
    shouldDemote: false,
    reason: `FP rate ${fpRate.toFixed(1)}% — grace ends at ${graceEnds.toISOString()}`,
    graceUntil: graceEnds.toISOString()
  }
}

// ─── Risk preview (for the promotion wizard) ─────────────────────────

export interface RiskPreview {
  totalEvaluations: number
  wouldBlockCount: number
  wouldBlockPercentage: number
  estimatedFalsePositives: number
  estimatedAffectedUsers: number
  topReasons: Array<{ reason: string; count: number; pct: number }>
}

/**
 * Build a risk preview from a Guideline instance's stats so admins can
 * see the blast radius before promoting.
 */
export function buildRiskPreview(
  instance: PolicyInstance,
  detectorBreakdown?: Record<string, number>
): RiskPreview {
  const totalEvaluations = instance.stats?.totalEvaluations30d ?? 0
  const wouldBlockCount = instance.stats?.totalHits30d ?? 0
  const wouldBlockPercentage =
    totalEvaluations > 0 ? (wouldBlockCount / totalEvaluations) * 100 : 0
  const estimatedFalsePositives = instance.stats?.falsePositives30d ?? 0
  // Rough estimate: assume each FP is a unique user (worst case)
  const estimatedAffectedUsers = Math.max(estimatedFalsePositives, Math.floor(wouldBlockCount * 0.3))

  const topReasons = detectorBreakdown
    ? Object.entries(detectorBreakdown)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 5)
        .map(([reason, count]) => ({
          reason,
          count,
          pct: wouldBlockCount > 0 ? (count / wouldBlockCount) * 100 : 0
        }))
    : []

  return {
    totalEvaluations,
    wouldBlockCount,
    wouldBlockPercentage,
    estimatedFalsePositives,
    estimatedAffectedUsers,
    topReasons
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────

export function policyClassLabel(cls: PolicyClass): string {
  return cls === "strict" ? "Strictly Enforced" : "Guideline"
}

export function policyClassIcon(cls: PolicyClass): string {
  return cls === "strict" ? "🛡️" : "📘"
}

export { classOf }
