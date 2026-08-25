/* API response types matching backend Pydantic schemas */

export interface TeamResponse {
  id: string;
  external_group_id: string;
  display_name: string;
  description: string | null;
  group_type: string | null;
  member_count: number;
}

export interface TeamMemberResponse {
  id: string;
  external_user_id: string;
  email: string | null;
  display_name: string;
  department: string | null;
  job_title: string | null;
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserResponse {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_email_verified: boolean;
  tenant_id: string | null;
  org_id: string | null;
  roles: string[];
  auth_source: string;
  created_at: string;
  updated_at: string;
}

export interface TenantResponse {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrgResponse {
  id: string;
  tenant_id: string;
  name: string;
  slug: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface InviteResponse {
  id: string;
  email: string;
  tenant_id: string;
  role: string;
  is_used: boolean;
  is_revoked: boolean;
  expires_at: string;
  created_at: string;
}

export interface UserActivityBlobResponse {
  id: string;
  tenant_id: string;
  org_id: string;
  source_type: string;
  source_id: string | null;
  title: string | null;
  content_preview: string;
  word_count: number;
  processing_status: string;
  batch_id: string | null;
  adapter_name: string;
  created_at: string;
  is_test_data: boolean;
}

export interface SourceOfTruthBlobResponse {
  id: string;
  tenant_id: string;
  org_id: string;
  title: string;
  content_preview: string;
  source_category: string;
  word_count: number;
  processing_status: string;
  batch_id: string | null;
  created_at: string;
  is_test_data: boolean;
}

export interface RiskAnalysisResponse {
  id: string;
  tenant_id: string;
  org_id: string;
  source_of_truth_blob_id: string;
  batch_id: string | null;
  risk_title: string;
  risk_description: string;
  risk_category: string;
  risk_severity: string;
  risk_likelihood: string;
  risk_score: number;
  confidence_score: number;
  mitigation_title: string;
  mitigation_description: string;
  mitigation_steps: string[];
  citations: Array<{ blob_id: string; snippet: string }>;
  model_used: string;
  processing_status: string;
  created_at: string;
  is_test_data: boolean;
}

export interface CorrelationResponse {
  id: string;
  tenant_id: string;
  org_id: string;
  user_activity_blob_ids: string[];
  risk_analysis_ids: string[];
  batch_id: string | null;
  correlation_title: string;
  correlation_summary: string;
  correlation_type: string;
  overall_risk_score: number;
  confidence_score: number;
  score_reliability: number;
  match_tags: string[];
  action_plan_title: string;
  action_plan_description: string;
  action_steps: Array<{ step: string; priority: string; effort: string; owner_type?: string }>;
  priority: string;
  estimated_effort: string | null;
  citations: Array<{ source_type: string; source_id: string; snippet: string }>;
  reasoning: string;
  status: string;
  is_excluded: boolean;
  excluded_by: string | null;
  excluded_at: string | null;
  exclusion_reason: string | null;
  model_used: string;
  created_at: string;
  is_test_data: boolean;
}

export interface DispatchEventResponse {
  id: string;
  tenant_id: string;
  org_id: string;
  correlation_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  severity: string;
  delivered: boolean;
  delivered_at: string | null;
  created_at: string;
  is_test_data: boolean;
}

export interface LLMUsageSummary {
  tenant_id: string;
  period_start: string;
  period_end: string;
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_estimated_cost_usd: number;
  by_model: Record<string, { requests: number; tokens: number; cost: number }>;
  by_operation: Record<string, { requests: number; tokens: number; cost: number }>;
}

export interface ErrorResponse {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
    correlation_id?: string;
  };
}

export type Role = 'SUPER_ADMIN' | 'TENANT_ADMIN' | 'ORG_ADMIN' | 'ANALYST' | 'VIEWER';

// ─── Integrations ────────────────────────────────────────────────────
//
// Mirror of backend's `app.schemas.integration`. The status / category
// / vendor enums match the Pydantic Literal[] declarations exactly.

export type IntegrationProvider =
  | 'MICROSOFT_ENTRA_ID'
  | 'MICROSOFT_INTUNE'
  | 'MICROSOFT_PURVIEW'
  | 'MICROSOFT_DEFENDER_CASB'
  | 'SLACK'
  | 'JAMF_PRO'
  | 'KANDJI'
  | 'JUMPCLOUD'
  | 'AWS'
  | 'AWS_IAM_IDENTITY_CENTER'
  | 'AWS_BEDROCK'
  | 'GCP'
  | 'GCP_WORKSPACE'
  | 'GCP_VERTEX'
  | 'SENTINEL';

export type IntegrationStatusServer =
  | 'NOT_CONNECTED'
  | 'CONNECTED'
  | 'ERROR'
  | 'DISABLED'
  | 'COMING_SOON';

export interface ProviderMetaPayload {
  provider: IntegrationProvider;
  display_name: string;
  short_name: string;
  category: 'identity' | 'device' | 'data' | 'communication' | 'cloud';
  vendor: 'microsoft' | 'slack' | 'aws' | 'gcp' | 'other';
  logo_slug: string;
  description: string;
  capabilities: string[];
  available: boolean;
  onboarding_recommended: boolean;
}

export interface IntegrationCardResponse {
  meta: ProviderMetaPayload;
  status: IntegrationStatusServer;
  integration_id: string | null;
  display_name: string;
  external_id: string | null;
  external_name: string | null;
  scopes: string[];
  last_synced_at: string | null;
  last_error: string | null;
  connected_at: string | null;
  /** Tenant-specific provider config JSON (e.g. Intune device-group filter). */
  config: Record<string, unknown>;
}

export interface IntegrationListResponse {
  integrations: IntegrationCardResponse[];
  total: number;
}

export interface InstallStartResponse {
  authorize_url: string;
  provider: IntegrationProvider;
}

// ─── Ardoq AI-Lens export (#248) ───────────────────────────────────────

export interface ArdoqManifestResponse {
  generated_at: string;
  totals: Record<string, number>;
}

// ─── Onboarding ──────────────────────────────────────────────────────

export interface OnboardingStatusResponse {
  tenant_id: string;
  tenant_name: string;
  is_complete: boolean;
  completed_at: string | null;
}

// ─── Use cases / registry ────────────────────────────────────────────

export type UseCaseStatus = 'DRAFT' | 'REVIEW' | 'ACTIVE' | 'RETIRED';
export type UseCaseRiskTier = 'LOW' | 'MEDIUM' | 'HIGH';
export type UseCaseSource = 'BOT' | 'FORM' | 'SHADOW_PROMOTE' | 'IMPORT' | 'VOICE_INTERVIEW';

export interface UseCaseResponse {
  id: string;
  tenant_id: string;
  title: string;
  tool: string;
  department: string;
  owner_user_id: string | null;
  status: UseCaseStatus;
  risk_tier: UseCaseRiskTier;
  source: UseCaseSource;
  data_classes: string[];
  frequency: string | null;
  notes: string | null;
  dispatched_from_response_id: string | null;
  created_at: string;
  updated_at: string;
  is_test_data: boolean;
}

export interface UseCaseListResponse {
  use_cases: UseCaseResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface UseCaseCounts {
  total: number;
  draft: number;
  review: number;
  active: number;
  retired: number;
}

// ─── Owner inference (#244) ─────────────────────────────────────────

export interface OwnerCandidate {
  user_id: string;
  display_name: string;
  email: string | null;
  confidence: number;
  rationale: string;
}

export interface OwnerSuggestion {
  use_case_id: string;
  use_case_title: string;
  department: string;
  business_owner_candidates: OwnerCandidate[];
  it_owner_candidates: OwnerCandidate[];
}

export interface OwnerSuggestionsListResponse {
  suggestions: OwnerSuggestion[];
  total_unowned: number;
}

// ─── Surveys ─────────────────────────────────────────────────────────

export type DeliveryChannel = 'SLACK' | 'EMAIL';
export type DeliveryStatus =
  | 'PENDING'
  | 'SENDING'
  | 'SENT'
  | 'FAILED'
  | 'CANCELED';
export type ResponseStatus =
  | 'NOT_STARTED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'OPTED_OUT'
  | 'EXPIRED';

export interface AudienceFilter {
  mode: 'all' | 'departments' | 'tools' | 'custom';
  values: string[];
}

export interface DispatchCreate {
  template_slug: string;
  template_version?: string;
  audience: AudienceFilter;
  channel?: DeliveryChannel;
  name?: string;
}

export interface DispatchResponse {
  id: string;
  tenant_id: string;
  template_id: string;
  template_slug: string;
  template_version: string;
  name: string;
  audience_label: string;
  audience: AudienceFilter;
  channel: DeliveryChannel;
  status: DeliveryStatus;
  sent_by_user_id: string | null;
  recipient_count: number;
  completed_count: number;
  new_registration_count: number;
  created_at: string;
  dispatched_at: string | null;
  completed_at: string | null;
}

export interface DispatchListResponse {
  dispatches: DispatchResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface SurveyTemplateResponse {
  id: string;
  tenant_id: string;
  slug: string;
  name: string;
  version: string;
  description: string | null;
  questions: Array<Record<string, unknown>>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface NextQuestionResponse {
  response_id: string;
  is_complete: boolean;
  next_question: Record<string, unknown> | null;
  answered_count: number;
  total_applicable: number;
}

// ─── Endpoint compliance (PR follow-up to MDM sync workers) ──────────

export interface EndpointMdmAggregateResponse {
  mdm_provider: string;
  display_name: string;
  vendor: 'microsoft' | 'other';
  enrolled_count: number;
  compliant_count: number;
  extension_installed_count: number;
  last_synced_at: string | null;
}

export interface EndpointComplianceResponse {
  aggregates: EndpointMdmAggregateResponse[];
  total_enrolled: number;
  total_compliant: number;
  total_extension_installed: number;
}

export interface EndpointDeviceServerResponse {
  id: string;
  mdm_provider: string;
  device_name: string;
  operating_system: string | null;
  os_version: string | null;
  enrolled_user_email: string | null;
  compliance_state: string | null;
  extension_installed: boolean;
  last_seen_at: string | null;
}

export interface EndpointDevicesResponse {
  devices: EndpointDeviceServerResponse[];
  total: number;
}

// ─── Handbook (Priority #1 from customer feedback) ───────────────────

export interface HandbookStatusResponse {
  current_version: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  acknowledged_version: string | null;
}

export interface HandbookAckResponse {
  user_id: string;
  version: string;
  acknowledged_at: string;
}

export interface HandbookCoverageItem {
  version: string;
  count: number;
}

/** Tenant-specific handbook content. `content_markdown` is null when
 *  the tenant has no override and is using the curated stock copy. */
export interface HandbookContentResponse {
  version: string;
  content_markdown: string | null;
  updated_at: string | null;
  is_stock: boolean;
}

export interface HandbookOverrideUpsert {
  version: string;
  content_markdown: string;
}

export interface HandbookTenantStats {
  tenant_id: string;
  current_version: string;
  total_users: number;
  acknowledged_current_count: number;
  acknowledged_any_count: number;
  by_version: HandbookCoverageItem[];
}

// ─── Use case reviews (Priority #3 — drift detection) ────────────────

export type ReviewStatusServer = 'DUE' | 'REVIEWED' | 'OVERDUE' | 'DISMISSED';

export interface UseCaseReviewResponse {
  id: string;
  tenant_id: string;
  use_case_id: string;
  use_case_title: string;
  use_case_tool: string;
  use_case_department: string;
  due_at: string;
  status: ReviewStatusServer;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  notes: string | null;
  drift_observed: boolean;
  dismiss_reason: string | null;
  created_at: string;
}

export interface UseCaseReviewListResponse {
  reviews: UseCaseReviewResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface UseCaseReviewCounts {
  due: number;
  overdue: number;
  reviewed: number;
  dismissed: number;
  drift_observed_count: number;
}

// ─── AI inventory (aggregate + bulk-import) ───────────────────────

export interface AiInventoryDimensionCount {
  label: string;
  count: number;
}

export interface AiInventoryAggregate {
  total: number;
  unowned_count: number;
  active_high_risk_count: number;
  by_status: Record<string, number>;
  by_risk_tier: Record<string, number>;
  by_source: Record<string, number>;
  by_department: AiInventoryDimensionCount[];
  by_tool: AiInventoryDimensionCount[];
}

export interface AiInventoryImportRowError {
  row_number: number;
  field: string | null;
  message: string;
}

export interface AiInventoryImportWarning {
  row_number: number;
  message: string;
}

export interface AiInventoryImportResponse {
  imported: number;
  errors: AiInventoryImportRowError[];
  warnings: AiInventoryImportWarning[];
  total_rows_seen: number;
}
