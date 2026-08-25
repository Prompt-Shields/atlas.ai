'use client';

/**
 * Typed client for the tenant-scoped AI-SPM domain APIs.
 *
 * Mirrors the response schemas served by `/ai-assets`, `/model-risk`, and
 * `/compliance`. Authentication and error handling stay centralized in
 * `api.ts`.
 */

import { api } from './api';

export interface PaginationOptions {
  page?: number;
  pageSize?: number;
}

export interface AIAsset {
  id: string;
  tenant_id: string;
  org_id: string;
  name: string;
  description: string | null;
  deployment_status: string;
  hosting_location: string | null;
  model_type: string | null;
  is_third_party: boolean;
  owner_id: string | null;
  risk_score: number | null;
  gdpr_status: string | null;
  eu_ai_act_status: string | null;
  nist_ai_rmf_status: string | null;
  eu_ai_act_risk_tier: string | null;
  accuracy: number | null;
  latency_ms: number | null;
  drift_score: number | null;
  monthly_calls: number | null;
  created_at: string;
  updated_at: string;
  is_test_data: boolean;
}

export interface AIAssetCreate {
  name: string;
  description?: string | null;
  deployment_status?: string;
  hosting_location?: string | null;
  model_type?: string | null;
  is_third_party?: boolean;
  owner_id?: string | null;
}

export type AIAssetUpdate = Partial<AIAssetCreate>;

export interface AIAssetListResponse {
  assets: AIAsset[];
  total: number;
  page: number;
  page_size: number;
}

export interface ModelRiskProfile {
  id: string;
  tenant_id: string;
  org_id: string;
  name: string;
  provider: string | null;
  category: string | null;
  parameters: string | null;
  lifecycle_status: string | null;
  overall_risk: number | null;
  hallucination_risk: number | null;
  bias_risk: number | null;
  toxicity_risk: number | null;
  privacy_risk: number | null;
  security_risk: number | null;
  compliance_risk: number | null;
  key_risks: string[] | null;
  approved_use_cases: string[] | null;
  risk_alerts: Array<Record<string, unknown>> | null;
  created_at: string;
  updated_at: string;
  is_test_data: boolean;
}

export interface ModelRiskProfileListResponse {
  profiles: ModelRiskProfile[];
  total: number;
  page: number;
  page_size: number;
}

export interface ComplianceAssessment {
  id: string;
  tenant_id: string;
  org_id: string;
  asset_id: string;
  framework: string;
  risk_level: string | null;
  passed: boolean;
  rationale: string | null;
  review_date: string | null;
  live_from: string | null;
  live_to: string | null;
  approval_status: string | null;
  approved_by_id: string | null;
  created_at: string;
  updated_at: string;
  is_test_data: boolean;
}

export interface ComplianceAssessmentListResponse {
  assessments: ComplianceAssessment[];
  total: number;
  page: number;
  page_size: number;
}

export interface ComplianceAssessmentListOptions extends PaginationOptions {
  framework?: string;
  passed?: boolean;
  assetId?: string;
}

function paginationQuery(options: PaginationOptions = {}): string {
  const query = new URLSearchParams();
  if (options.page) query.set('page', String(options.page));
  if (options.pageSize) query.set('page_size', String(options.pageSize));
  const value = query.toString();
  return value ? `?${value}` : '';
}

export function listAIAssets(
  options: PaginationOptions = {},
): Promise<AIAssetListResponse> {
  return api.request<AIAssetListResponse>(`/ai-assets${paginationQuery(options)}`);
}

export function getAIAsset(id: string): Promise<AIAsset> {
  return api.request<AIAsset>(`/ai-assets/${id}`);
}

export function createAIAsset(body: AIAssetCreate): Promise<AIAsset> {
  return api.request<AIAsset>('/ai-assets', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function updateAIAsset(
  id: string,
  body: AIAssetUpdate,
): Promise<AIAsset> {
  return api.request<AIAsset>(`/ai-assets/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function listModelRiskProfiles(
  options: PaginationOptions = {},
): Promise<ModelRiskProfileListResponse> {
  return api.request<ModelRiskProfileListResponse>(
    `/model-risk${paginationQuery(options)}`,
  );
}

export function getModelRiskProfile(id: string): Promise<ModelRiskProfile> {
  return api.request<ModelRiskProfile>(`/model-risk/${id}`);
}

export function listComplianceAssessments(
  options: ComplianceAssessmentListOptions = {},
): Promise<ComplianceAssessmentListResponse> {
  const query = new URLSearchParams();
  if (options.page) query.set('page', String(options.page));
  if (options.pageSize) query.set('page_size', String(options.pageSize));
  if (options.framework) query.set('framework', options.framework);
  if (options.passed !== undefined) query.set('passed', String(options.passed));
  if (options.assetId) query.set('asset_id', options.assetId);
  const value = query.toString();
  return api.request<ComplianceAssessmentListResponse>(
    `/compliance${value ? `?${value}` : ''}`,
  );
}
