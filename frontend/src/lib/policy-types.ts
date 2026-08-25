// Policy Template + Instance type system
// Templates are read-only, shipped with the product. Users clone them into Instances.

export type PolicyCategory =
  | "OWASP_LLM"
  | "EU_AI_ACT"
  | "GDPR"
  | "INDUSTRY"
  | "SHADOW_AI"
  | "CONTENT_SAFETY"
  | "CUSTOM"

export type Severity = "low" | "medium" | "high" | "critical"

export type EnforcementMode = "log" | "flag" | "block" | "redact"

// ─── Triggers: when does the policy evaluate? ────────────────────────

export type TriggerStage = "input" | "output" | "context" | "tool_call"

export interface Trigger {
  stage: TriggerStage
  description: string
}

// ─── Detectors: how does it identify a violation? ────────────────────

export type DetectorType =
  | "regex"
  | "keyword_list"
  | "classifier"
  | "llm_judge"
  | "rate_counter"
  | "entropy"
  | "pii_detector"
  | "secrets_scanner"

export interface Detector {
  id: string
  type: DetectorType
  description: string
  // Detector-specific config — keyed by parameter id so user-tunable
  configRef: string
}

// ─── Actions: what happens on a hit? ─────────────────────────────────

export type ActionType =
  | "block"
  | "flag"
  | "log"
  | "redact"
  | "rewrite"
  | "notify"
  | "require_review"

export interface Action {
  type: ActionType
  description: string
  configRef?: string
}

// ─── Tunable parameters: the knobs in the editor ─────────────────────

export type ParameterType =
  | "number"
  | "string"
  | "boolean"
  | "list"
  | "regex"
  | "keywords"
  | "channel"

export interface Parameter {
  key: string
  label: string
  type: ParameterType
  default: unknown
  min?: number
  max?: number
  step?: number
  options?: string[]
  helpText: string
  level: "basic" | "advanced" | "expert"
  locked?: boolean
}

// ─── The Template (read-only, shipped) ───────────────────────────────

export interface PolicyTemplate {
  id: string
  name: string
  version: string
  category: PolicyCategory
  author: string

  // Classification
  owaspReference?: string
  regulatoryReferences: string[]
  severity: Severity

  // Human-readable
  description: string
  rationale: string
  exampleViolation: string
  exampleSafeInput?: string

  // Machine-readable logic
  triggers: Trigger[]
  detectors: Detector[]
  actions: Action[]

  // Tuning surface
  tunableParameters: Parameter[]

  // Defaults for new instances
  defaults: {
    enforcementMode: EnforcementMode
    appliesTo: {
      dataClassifications?: string[]
      riskTiers?: string[]
      departments?: string[]
    }
  }

  // Metadata
  tags: string[]
  createdAt: string
  updatedAt: string
}

// ─── Policy class (UX-level concept) ─────────────────────────────────
// Derived from enforcementMode but used as the primary visual signal.
// Guideline = advisory (log/flag). Strict = real-time enforcement (block/redact).

export type PolicyClass = "guideline" | "strict"

export function classOf(mode: EnforcementMode): PolicyClass {
  return mode === "block" || mode === "redact" ? "strict" : "guideline"
}

// ─── Promotion lifecycle ─────────────────────────────────────────────

export interface ApprovalRecord {
  role: string // e.g. "DPO", "Security Lead"
  status: "pending" | "approved" | "rejected"
  approverId?: string
  at?: string
  notes?: string
}

export interface PromotionEligibility {
  eligible: boolean
  daysInGuideline: number
  daysRequired: number
  falsePositiveRate: number
  fpRateThreshold: number
  approvers: ApprovalRecord[]
  blockingReasons: string[]
}

export interface AutoDemoteConfig {
  enabled: boolean
  fpRateThreshold: number // % above which auto-demote triggers (e.g. 5 = 5%)
  windowMinutes: number // rolling window for FP measurement
  graceSeconds: number // countdown before auto-demote fires
}

export type RolloutStrategy = "all" | "canary" | "phased"

export interface PromotionEvent {
  from: PolicyClass
  to: PolicyClass
  at: string
  by: string
  reason?: string
  approvers?: ApprovalRecord[]
}

// ─── The Instance (user-owned, editable) ─────────────────────────────

export type InstanceStatus = "draft" | "testing" | "active" | "paused" | "archived"

export interface PolicyInstance {
  // Identity
  id: string
  name: string
  templateId: string
  templateVersion: string // captured at clone time — used for upgrade detection

  // User-tuned values for the template's parameters
  parameterValues: Record<string, unknown>

  // User-chosen enforcement
  enforcementMode: EnforcementMode
  severity: Severity // can override template default

  // Scope
  appliesTo: {
    applicationIds: string[]
    dataClassifications: string[]
    riskTiers: string[]
    departments: string[]
  }

  // Exception handling
  allowList: string[] // user identifiers / email patterns exempted
  reviewers: string[] // person IDs who can override blocks

  // Notifications
  notifications: {
    onBlock?: string[] // channel IDs
    onFlag?: string[]
    onLog?: string[]
  }

  // Lifecycle
  status: InstanceStatus
  createdBy: string
  createdAt: string
  updatedAt: string
  activatedAt?: string

  // Promotion lifecycle (Guideline ⇄ Strict)
  promotionHistory: PromotionEvent[]
  autoDemote: AutoDemoteConfig
  rolloutStrategy: RolloutStrategy
  rolloutPercentage?: number // for "phased" — current percentage (0-100)
  rolloutCanaryAppId?: string // for "canary" — single app the policy is live on

  // Stats (populated from violation feed)
  stats?: {
    totalEvaluations30d: number
    totalHits30d: number
    blockCount30d: number
    flagCount30d: number
    lastTriggeredAt?: string
    falsePositives30d?: number
    falsePositiveRate?: number // 0-1
  }
}

// ─── Violations (evidence, populated from PEP webhooks) ──────────────

export interface PolicyViolation {
  id: string
  policyInstanceId: string
  applicationId: string
  timestamp: string
  actionTaken: ActionType
  severity: Severity
  detectorId: string
  promptHash: string // never store raw prompts
  user?: string
  evidence: {
    detectorOutput: string
    matchedPattern?: string
    confidence?: number
  }
  reviewed: boolean
  reviewedBy?: string
  reviewNotes?: string
}

// ─── Test console types ──────────────────────────────────────────────

export interface PolicyTestInput {
  prompt: string
  expectedOutput?: string
  context?: Record<string, unknown>
}

export interface PolicyTestResult {
  matched: boolean
  triggeredDetectors: Array<{
    detectorId: string
    confidence: number
    matchedSubstring?: string
    explanation: string
  }>
  actionThatWouldFire: ActionType | "none"
  evaluationTimeMs: number
}
