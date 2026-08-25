'use client';

/**
 * SaaS Vendor AI API client and types (SP-1).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/saas_vendor.py` and the
 * `/api/v1/saas-vendor-ai` router. Delegates auth/refresh to the shared
 * ApiClient in `api.ts`.
 */

import { api } from './api';

// ──────────────────────────────────────────────────────────────────────
// Enums (string unions mirroring the backend enums)
// ──────────────────────────────────────────────────────────────────────

export type VendorCategory =
  | 'llm_provider'
  | 'productivity_suite'
  | 'developer_tool'
  | 'crm_support'
  | 'data_analytics';

export type VendorDiscoveryMethod =
  | 'endpoint'
  | 'browser_extension'
  | 'sso'
  | 'integration'
  | 'manual';

export type VendorContractStatus = 'active' | 'pending_renewal' | 'expired';

export type AssessmentStatus = 'draft' | 'sent' | 'received' | 'reviewed';

export type AssessmentImportSource = 'peer_shared' | 'trust_center' | 'upload';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export interface VendorDataFlow {
  description: string;
  classification: string;
  trains_on_data: boolean;
}

/** Vendor-only profile fields shared by create/response payloads. */
export interface VendorProfileFields {
  category: VendorCategory;
  ai_feature: string;
  discovered_via: VendorDiscoveryMethod;
  risk_score: number;
  models: string[];
  sub_processors: string[];
  data_flows: VendorDataFlow[];
  certifications: string[];
  compliance_status: Record<string, string>;
  dpa: boolean;
  training_opt_out: boolean;
  contract_status: VendorContractStatus;
  last_reviewed_at: string | null;
}

export interface SaaSVendor extends VendorProfileFields {
  id: string;
  name: string;
  department: string;
  owner_user_id: string | null;
  source: string;
  has_assessment: boolean;
}

export type SaaSVendorCreate = Partial<VendorProfileFields> & {
  name: string;
  department: string;
  category: VendorCategory;
  owner_user_id?: string | null;
};

export type SaaSVendorUpdate = Partial<
  VendorProfileFields & {
    name: string;
    department: string;
    owner_user_id: string | null;
  }
>;

export interface VendorKpi {
  total: number;
  high_risk: number;
  trains_on_data: number;
  no_dpa: number;
}

export interface AssessmentRequestCreate {
  questionnaire?: string;
  notes?: string | null;
}

export interface AssessmentRequest {
  id: string;
  use_case_id: string;
  questionnaire: string;
  status: AssessmentStatus;
  token: string | null;
  sent_at: string | null;
  received_at: string | null;
  notes: string | null;
}

export interface AssessmentImportCreate {
  source: AssessmentImportSource;
  reference?: string | null;
  payload?: Record<string, unknown>;
}

export interface AssessmentImport {
  id: string;
  use_case_id: string;
  source: AssessmentImportSource;
  reference: string | null;
  payload: Record<string, unknown>;
  imported_at: string;
}

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/saas-vendor-ai';

export const vendorsApi = {
  list: (): Promise<SaaSVendor[]> => api.request<SaaSVendor[]>(BASE),

  get: (id: string): Promise<SaaSVendor> =>
    api.request<SaaSVendor>(`${BASE}/${id}`),

  kpi: (): Promise<VendorKpi> => api.request<VendorKpi>(`${BASE}/kpi`),

  /** Raw CSV export body (text/csv). */
  exportCsv: (): Promise<string> => api.requestText(`${BASE}/export.csv`),

  create: (body: SaaSVendorCreate): Promise<SaaSVendor> =>
    api.request<SaaSVendor>(BASE, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  update: (id: string, body: SaaSVendorUpdate): Promise<SaaSVendor> =>
    api.request<SaaSVendor>(`${BASE}/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  requestAssessment: (
    id: string,
    body: AssessmentRequestCreate = {},
  ): Promise<AssessmentRequest> =>
    api.request<AssessmentRequest>(`${BASE}/${id}/request-assessment`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  importAssessment: (
    id: string,
    body: AssessmentImportCreate,
  ): Promise<AssessmentImport> =>
    api.request<AssessmentImport>(`${BASE}/${id}/import-assessment`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
};
