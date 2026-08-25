// Canonical wire schemas for the AI-SPM entity stores.
// One TS source of truth per Ardoq AI Lens entity — see
// `ardoq-import/README.md` for the import target shapes and
// `docs/ai-spm-crud-plan.md` for the decisions that shaped these types.

// ─── Common types ────────────────────────────────────────────────────

/// Human-readable, kebab-case identifier (e.g. `app-contract-review`).
/// Used as both the local primary key and the Ardoq Custom ID field.
/// Decision #1: kebab-case wins over opaque UUIDs.
export type CustomId = string

export type Tag = string

/// Tenant identifier. Decision #2: every entity carries it from day one,
/// seeded to `"default"` until multi-tenancy lands. No public API takes it
/// as a parameter today; the request middleware will populate it later.
export type TenantId = string

export const DEFAULT_TENANT_ID: TenantId = "default"

/// Mixin for entities that have create/update audit timestamps.
export interface Timestamped {
  createdAt: string
  updatedAt: string
}

/// Mixin for tenant-scoped records.
export interface TenantScoped {
  tenantId: TenantId
}

// ─── Shared enums ────────────────────────────────────────────────────

export type DataClassification = "public" | "internal" | "confidential" | "restricted"
export type DeploymentStatus = "Active" | "Shadow" | "Deprecated" | "Testing"

// ─── Person ──────────────────────────────────────────────────────────

/// Auth0 `sub` claim. Decision #3: this is the natural join key for SCIM
/// sync later; we keep it alongside the kebab-case CustomId.
export type Auth0Sub = string

export interface Person extends TenantScoped, Timestamped {
  id: CustomId                       // e.g. `person-sarah-chen`
  auth0Sub?: Auth0Sub                // populated when the macOS app upserts
  componentName: string              // display name "Sarah Chen"
  description: string
  email: string
  role: string
  organizationalUnitId?: CustomId
  tags: Tag[]
}

// ─── Organizational Unit ─────────────────────────────────────────────

export interface OrganizationalUnit extends TenantScoped, Timestamped {
  id: CustomId                       // e.g. `org-legal`
  componentName: string              // "Legal"
  description: string
  parentId?: CustomId                // for nested orgs
  tags: Tag[]
}

// ─── Application ─────────────────────────────────────────────────────

export interface Application extends TenantScoped, Timestamped {
  id: CustomId                       // e.g. `app-contract-review`
  componentName: string
  description: string
  organizationalUnitId?: CustomId    // Department
  ownerPersonId?: CustomId
  dataClassification?: DataClassification
  deploymentStatus: DeploymentStatus
  riskScore: number                  // 0–100, computed
  tags: Tag[]

  /// Auto-discovery flag. True when the macOS Promptly app created this
  /// stub from observed Shadow AI usage (decision #5: human always
  /// promotes from Shadow → Active).
  autoDiscovered: boolean

  /// The Promptly MonitoredApp.id that triggered the auto-discovery
  /// (e.g. `chatgpt`, `claude`, `notion`). Used to dedupe further usage
  /// events into the same Application.
  autoDiscoveredFromAppId?: string
}

// ─── Data Store ──────────────────────────────────────────────────────

export type StoreType =
  | "Relational Database"
  | "Document Store"
  | "Object Store"
  | "Data Warehouse"
  | "Cache"
  | "Search Index"
  | "Other"

export interface DataStore extends TenantScoped, Timestamped {
  id: CustomId                       // e.g. `ds-crm`
  componentName: string
  description: string
  storeType: StoreType
  dataClassification: DataClassification
  location: string                   // free text: "Azure Norway East"
  recordCount?: string               // free text: "2.4M customers"
  refreshFrequency?: string          // "Real-time" / "Daily"
  retentionPeriod?: string           // "7 years"
  encryption?: string
  accessControls?: string
  gdprCompliant: boolean
  dataOwnerId?: CustomId             // Person ref
  lastAudit?: string                 // ISO date
  tags: Tag[]
}

// ─── Technology Service ──────────────────────────────────────────────

export type ServiceType =
  | "Cloud Infrastructure"
  | "AI Platform"
  | "API Gateway"
  | "Identity Provider"
  | "Database Service"
  | "Other"

export interface TechnologyService extends TenantScoped, Timestamped {
  id: CustomId                       // e.g. `svc-azure-openai`
  componentName: string
  description: string
  provider: string                   // "Azure", "AWS", "OpenAI"
  serviceType: ServiceType
  region: string
  status: "Active" | "Deprecated"
  uptimePct?: number
  costPerMonthNOK?: number
  securityCertifications: string[]
  networkIsolation?: string
  scalingPolicy?: string
  backupStrategy?: string
  tags: Tag[]
}

// ─── Technology Product ──────────────────────────────────────────────

export type ModelCategory =
  | "LLM"
  | "Computer Vision"
  | "Speech"
  | "Image Generation"
  | "Machine Learning"
  | "Other"

export interface TechnologyProduct extends TenantScoped, Timestamped {
  id: CustomId                       // e.g. `tp-gpt4o`
  componentName: string              // "GPT-4o"
  description: string
  provider: string                   // "OpenAI"
  version: string
  modelCategory: ModelCategory
  parameters?: string                // "1.8 trillion"
  lifecycleStatus: "Production" | "Evaluation" | "Deprecated"
  inputCostPerMTokens?: string
  outputCostPerMTokens?: string
  contextWindow?: string             // "128K tokens"
  estimatedMonthlySpendNOK?: number
  tags: Tag[]

  /// Promptly hint: which MonitoredApp ids does this product back?
  /// (e.g. `["chatgpt"]` for GPT-4o because chat.openai.com defaults to it.)
  promptlyAppIds: string[]
}

// ─── Technical Capability ────────────────────────────────────────────
// Read-only seeded hierarchy; required by Ardoq AI Lens to detect AI Systems.

export interface TechnicalCapability extends TenantScoped, Timestamped {
  id: CustomId
  level1: string                     // "Artificial Intelligence" (root, exact name)
  level2?: string                    // "LLM", "Computer Vision", ...
  level3?: string
  description: string
  tags: Tag[]
}

// ─── Reference (entity edges) ────────────────────────────────────────

export type ReferenceType =
  | "Is Realized By"   // TechnicalCapability → TechnologyProduct
  | "Deploys"          // Application → TechnologyProduct
  | "Is Owner Of"      // Person → Application (formally assigned)
  | "Observed Use"     // Person → Application (decision #6: separate from "Is Owner Of")
  | "Belongs To"       // Person → OrganizationalUnit
  | "Reads From"       // Application → DataStore
  | "Runs On"          // Application → TechnologyService
  | "Has Subject"      // ComplianceAssessment → Application

export interface Reference extends TenantScoped, Timestamped {
  id: string                         // opaque — references are append-only
  sourceId: CustomId
  targetId: CustomId
  type: ReferenceType
  description: string
  /// Edges materialised from telemetry (Observed Use, Belongs To via
  /// Person.organizationalUnitId, etc.) get `autoDerived: true` and may
  /// be recomputed daily. Manual edges stick.
  autoDerived: boolean
}

// ─── Compliance Assessment (decision #4: separate first-class entity) ─

export type AssessmentType = "EU AI Act" | "GDPR" | "NIST AI RMF" | "ISO 42001" | "Other"
export type ApprovalStatus = "Approved" | "Under Review" | "Rejected"
export type EUAIActRiskLevel =
  | "Unacceptable Risk"
  | "High-Risk"
  | "Limited Risk"
  | "Minimal Risk"

export interface ComplianceAssessment extends TenantScoped, Timestamped {
  id: CustomId                       // e.g. `ca-contract-review-euai`
  componentName: string
  description: string
  assessmentType: AssessmentType
  subjectApplicationId: CustomId     // foreign key to Application
  pass: boolean
  rationale: string
  reviewDate: string                 // ISO date
  approvalStatus: ApprovalStatus
  approvedByPersonId?: CustomId      // Person ref
  euAIActRiskLevel?: EUAIActRiskLevel
  /// Optional pointer to the policy instance that produced the
  /// supporting evidence (when the assessment was driven by automated
  /// detection rather than a manual review).
  policyInstanceId?: string
  tags: Tag[]
}

// ─── Helpers ─────────────────────────────────────────────────────────

export function nowIso(): string {
  return new Date().toISOString()
}

/// Slugify a free-text component name into a kebab-case CustomId
/// suffix. Idempotent: `"Sarah Chen"` → `"sarah-chen"`.
export function slugify(input: string): string {
  return input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)+/g, "")
}
