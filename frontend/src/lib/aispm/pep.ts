'use client';

/**
 * LLM Policy Enforcement Point (PEP) API client and types (M2, issue #239).
 * LLM Policy Enforcement Point (PEP) API client and types (M2, issue #240).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/policy_enforcement.py`
 * and the `/api/v1/pep` router landed in #237/#238 — wire fields stay
 * snake_case to match the JSON exactly, same convention as
 * `lib/aispm/agent-control.ts`. Distinct from the unrelated
 * `lib/policy-types.ts` domain (the Intune-style `/dashboard/policies`
 * page) — do not conflate the two.
 */

import { api } from '../api';

// ──────────────────────────────────────────────────────────────────────
// Enums (string unions mirroring the backend enums)
// ──────────────────────────────────────────────────────────────────────

export type PolicyCategory =
  | 'OWASP_LLM'
  | 'EU_AI_ACT'
  | 'GDPR'
  | 'INDUSTRY'
  | 'SHADOW_AI'
  | 'CONTENT_SAFETY'
  | 'CUSTOM';

export type PolicySeverity = 'low' | 'medium' | 'high' | 'critical';

export type EnforcementMode = 'log' | 'flag' | 'block' | 'redact';

export type PolicyInstanceStatus = 'draft' | 'testing' | 'active' | 'paused' | 'archived';

export type RolloutStrategy = 'all' | 'canary' | 'phased';

export type PolicyClass = 'guideline' | 'strict';

/** Guideline = advisory (log/flag). Strict = real-time enforcement (block/redact). */
export function classOf(mode: EnforcementMode): PolicyClass {
  return mode === 'block' || mode === 'redact' ? 'strict' : 'guideline';
}

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export interface PolicyTemplate {
  id: string;
  slug: string;
  name: string;
  version: string;
  category: PolicyCategory;
  author: string;
  owasp_reference: string | null;
  regulatory_references: string[];
  severity: PolicySeverity;
  description: string;
  rationale: string;
  example_violation: string;
  example_safe_input: string | null;
  triggers: Array<Record<string, unknown>>;
  detectors: Array<Record<string, unknown>>;
  actions: Array<Record<string, unknown>>;
  tunable_parameters: Array<Record<string, unknown>>;
  default_enforcement_mode: EnforcementMode;
  default_applies_to: Record<string, unknown>;
  tags: string[];
}

export interface PolicyInstanceStats {
  total_evaluations_30d?: number;
  total_hits_30d?: number;
  block_count_30d?: number;
  flag_count_30d?: number;
  last_triggered_at?: string;
  false_positives_30d?: number;
  false_positive_rate?: number;
}

export interface PromotionEvent {
  from: PolicyClass;
  to: PolicyClass;
  at: string;
  by: string;
  reason?: string;
  approvers?: Array<{ role: string; status: string }>;
}

export interface PolicyInstance {
  id: string;
  name: string;
  template_id: string;
  template_version: string;
  parameter_values: Record<string, unknown>;
  enforcement_mode: EnforcementMode;
  severity: PolicySeverity;
  applies_to: Record<string, unknown>;
  allow_list: string[];
  reviewers: string[];
  notifications: Record<string, unknown>;
  status: PolicyInstanceStatus;
  created_by_user_id: string | null;
  activated_at: string | null;
  promotion_history: PromotionEvent[];
  auto_demote: Record<string, unknown>;
  rollout_strategy: RolloutStrategy;
  rollout_percentage: number | null;
  rollout_canary_app_id: string | null;
  stats: PolicyInstanceStats;
}

export interface PolicyApproverStatus {
  role: string;
  status: string;
  approved_by: string | null;
  approved_at: string | null;
}

export interface PolicyEligibility {
  eligible: boolean;
  days_in_guideline: number;
  days_required: number;
  false_positive_rate: number;
  fp_rate_threshold: number;
  approvers: PolicyApproverStatus[];
  blocking_reasons: string[];
}

export interface PolicyInstanceCloneRequest {
  name?: string;
  applies_to?: Record<string, unknown>;
  parameter_values?: Record<string, unknown>;
}

export interface PolicyApproveRequest {
  role: string;
  comment?: string;
}

export interface PolicyPromoteRequest {
  rollout_strategy?: RolloutStrategy;
  rollout_canary_app_id?: string;
  rollout_percentage?: number;
  target_mode?: EnforcementMode;
}

export interface PolicyDemoteRequest {
  reason?: string;
}

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/pep';

export const pepApi = {
  listTemplates: (): Promise<PolicyTemplate[]> => api.request<PolicyTemplate[]>(`${BASE}/templates`),

  listPolicies: (): Promise<PolicyInstance[]> => api.request<PolicyInstance[]>(`${BASE}/policies`),

  getTemplate: (templateId: string): Promise<PolicyTemplate> =>
    api.request<PolicyTemplate>(`${BASE}/templates/${templateId}`),

  getPolicy: (instanceId: string): Promise<PolicyInstance> =>
    api.request<PolicyInstance>(`${BASE}/policies/${instanceId}`),

  cloneTemplate: (
    templateId: string,
    payload: PolicyInstanceCloneRequest = {},
  ): Promise<PolicyInstance> =>
    api.request<PolicyInstance>(`${BASE}/policies/${templateId}/clone`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getEligibility: (instanceId: string): Promise<PolicyEligibility> =>
    api.request<PolicyEligibility>(`${BASE}/policies/${instanceId}/eligibility`),

  approve: (instanceId: string, payload: PolicyApproveRequest): Promise<PolicyEligibility> =>
    api.request<PolicyEligibility>(`${BASE}/policies/${instanceId}/approve`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  promote: (instanceId: string, payload: PolicyPromoteRequest = {}): Promise<PolicyInstance> =>
    api.request<PolicyInstance>(`${BASE}/policies/${instanceId}/promote`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  demote: (instanceId: string, payload: PolicyDemoteRequest = {}): Promise<PolicyInstance> =>
    api.request<PolicyInstance>(`${BASE}/policies/${instanceId}/demote`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};
