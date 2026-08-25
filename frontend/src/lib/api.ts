'use client';

import type {
  AiInventoryAggregate,
  ArdoqManifestResponse,
  AiInventoryImportResponse,
  AudienceFilter,
  DispatchCreate,
  DispatchListResponse,
  DispatchResponse,
  EndpointComplianceResponse,
  EndpointDevicesResponse,
  ErrorResponse,
  HandbookAckResponse,
  HandbookContentResponse,
  HandbookOverrideUpsert,
  HandbookStatusResponse,
  HandbookTenantStats,
  ReviewStatusServer,
  UseCaseReviewCounts,
  UseCaseReviewListResponse,
  UseCaseReviewResponse,
  InstallStartResponse,
  IntegrationCardResponse,
  IntegrationListResponse,
  IntegrationProvider,
  NextQuestionResponse,
  OnboardingStatusResponse,
  OwnerSuggestionsListResponse,
  SurveyTemplateResponse,
  TokenResponse,
  UseCaseCounts,
  UseCaseListResponse,
  UseCaseResponse,
  UseCaseRiskTier,
  UseCaseSource,
  UseCaseStatus,
} from './types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

function setCookie(name: string, value: string, days: number): void {
  const expires = new Date(Date.now() + days * 864e5).toUTCString();
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/; SameSite=Strict${secure}`;
}

function deleteCookie(name: string): void {
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; SameSite=Strict${secure}`;
}

class ApiClient {
  private accessToken: string | null = null;
  private refreshToken: string | null = null;

  constructor() {
    if (typeof window !== 'undefined') {
      this.accessToken = sessionStorage.getItem('access_token');
    }
  }

  setTokens(access: string, refresh: string): void {
    this.accessToken = access;
    this.refreshToken = refresh;
    if (typeof window !== 'undefined') {
      sessionStorage.setItem('access_token', access);
      setCookie('access_token', access, 1);
    }
  }

  clearTokens(): void {
    this.accessToken = null;
    this.refreshToken = null;
    if (typeof window !== 'undefined') {
      sessionStorage.removeItem('access_token');
      deleteCookie('access_token');
    }
  }

  get isAuthenticated(): boolean {
    return !!this.accessToken;
  }

  /** Full URL the "Sign in with Microsoft" button navigates to (server-side redirect). */
  get ssoMicrosoftLoginUrl(): string {
    return `${API_URL}/auth/sso/microsoft/login`;
  }

  /**
   * Typed fetch wrapper — the canonical way to call the backend.
   * Feature modules (e.g. developer.ts) should use this rather than
   * keeping their own copy: it handles the Authorization header,
   * 401 refresh-and-retry, and both backend error envelopes.
   */
  async request<T>(
    path: string,
    options: RequestInit = {},
    apiKey?: string,
  ): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string>),
    };

    if (this.accessToken) {
      headers['Authorization'] = `Bearer ${this.accessToken}`;
    }
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }

    const response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers,
    });

    if (response.status === 401 && this.refreshToken) {
      const refreshed = await this.refreshTokens();
      if (refreshed) {
        headers['Authorization'] = `Bearer ${this.accessToken}`;
        const retryResponse = await fetch(`${API_URL}${path}`, {
          ...options,
          headers,
        });
        if (!retryResponse.ok) {
          throw await this.parseError(retryResponse);
        }
        if (retryResponse.status === 204) return {} as T;
        return retryResponse.json();
      }
      this.clearTokens();
      throw new Error('Session expired. Please log in again.');
    }

    if (!response.ok) {
      throw await this.parseError(response);
    }

    if (response.status === 204) return {} as T;
    return response.json();
  }

  /**
   * Like `request`, but for endpoints that return a non-JSON body (e.g. a
   * `text/csv` export). Sends the auth header and throws on non-2xx, but
   * returns the raw response text instead of parsing JSON.
   */
  async requestText(path: string, options: RequestInit = {}): Promise<string> {
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };
    if (this.accessToken) {
      headers['Authorization'] = `Bearer ${this.accessToken}`;
    }
    const response = await fetch(`${API_URL}${path}`, { ...options, headers });
    if (!response.ok) {
      throw await this.parseError(response);
    }
    return response.text();
  }

  /**
   * Like `requestText`, but for binary downloads (e.g. a `application/zip`
   * export). Sends the auth header and throws on non-2xx, but returns the
   * raw `Blob` instead of parsing text or JSON.
   */
  async requestBlob(path: string, options: RequestInit = {}): Promise<Blob> {
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };
    if (this.accessToken) {
      headers['Authorization'] = `Bearer ${this.accessToken}`;
    }
    const response = await fetch(`${API_URL}${path}`, { ...options, headers });
    if (!response.ok) {
      throw await this.parseError(response);
    }
    return response.blob();
  }

  private async parseError(response: Response): Promise<Error> {
    try {
      const body = (await response.json()) as Partial<ErrorResponse> & {
        detail?: unknown;
      };
      // Global handlers (app/errors.py) wrap errors as { error: { message } },
      // but routers that raise FastAPI's HTTPException directly produce
      // { detail: "..." } — accept both envelopes.
      const detail =
        typeof body.detail === 'string'
          ? body.detail
          : body.detail !== undefined
            ? JSON.stringify(body.detail)
            : undefined;
      return new Error(
        body.error?.message || detail || `HTTP ${response.status}`,
      );
    } catch {
      return new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
  }

  private async refreshTokens(): Promise<boolean> {
    try {
      const response = await fetch(`${API_URL}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: this.refreshToken }),
      });
      if (!response.ok) return false;
      const data: TokenResponse = await response.json();
      this.setTokens(data.access_token, data.refresh_token);
      return true;
    } catch {
      return false;
    }
  }

  // ── Auth ────────────────────────────────────────────────────────
  async login(email: string, password: string): Promise<TokenResponse> {
    const data = await this.request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    this.setTokens(data.access_token, data.refresh_token);
    return data;
  }

  /** Exchange a one-time SSO handoff code (from the callback redirect) for a session. */
  async exchangeSsoCode(code: string): Promise<TokenResponse> {
    const data = await this.request<TokenResponse>('/auth/sso/exchange', {
      method: 'POST',
      body: JSON.stringify({ code }),
    });
    this.setTokens(data.access_token, data.refresh_token);
    return data;
  }

  async logout(): Promise<void> {
    try {
      if (this.accessToken) {
        await fetch(`${API_URL}/auth/logout`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${this.accessToken}`,
            'Content-Type': 'application/json',
          },
        });
      }
    } catch {
      // Best-effort
    } finally {
      this.clearTokens();
    }
  }

  // ── Users ───────────────────────────────────────────────────────
  async getMe() { return this.request<any>('/users/me'); }
  async listUsers(opts: { page?: number; q?: string; role?: string; is_active?: boolean; source?: string } = {}) {
    const p = new URLSearchParams({ page: String(opts.page ?? 1) });
    if (opts.q) p.set('q', opts.q);
    if (opts.role) p.set('role', opts.role);
    if (opts.is_active !== undefined) p.set('is_active', String(opts.is_active));
    if (opts.source) p.set('source', opts.source);
    return this.request<any>(`/users?${p.toString()}`);
  }
  async createUser(data: any) { return this.request<any>('/users', { method: 'POST', body: JSON.stringify(data) }); }
  async updateUser(id: string, data: any) { return this.request<any>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }); }

  // ── Teams (synced Entra directory groups) ───────────────────────
  async listTeams() { return this.request<any>('/teams'); }
  async listTeamMembers(teamId: string) { return this.request<any>(`/teams/${teamId}/members`); }

  // ── Tenants ─────────────────────────────────────────────────────
  async listTenants() { return this.request<any[]>('/tenants'); }
  async createTenant(data: any) { return this.request<any>('/tenants', { method: 'POST', body: JSON.stringify(data) }); }
  async getTenant(id: string) { return this.request<any>(`/tenants/${id}`); }
  async listOrgs(tenantId: string) { return this.request<any[]>(`/tenants/${tenantId}/organisations`); }
  async createOrg(tenantId: string, data: any) { return this.request<any>(`/tenants/${tenantId}/organisations`, { method: 'POST', body: JSON.stringify(data) }); }

  // ── Invites ─────────────────────────────────────────────────────
  async createInvite(data: any) { return this.request<any>('/invites', { method: 'POST', body: JSON.stringify(data) }); }
  async confirmInvite(data: any) { return this.request<any>('/invites/confirm', { method: 'POST', body: JSON.stringify(data) }); }
  async listInvites() { return this.request<any>('/invites'); }

  // ── Adapters (User Activity) ────────────────────────────────────
  async listUserActivityBlobs(page = 1) { return this.request<any>(`/adapters/blobs?page=${page}`); }
  async ingestManual(data: any, apiKey: string) { return this.request<any>('/adapters/manual/ingest', { method: 'POST', body: JSON.stringify(data) }, apiKey); }

  // ── Sources of Truth ────────────────────────────────────────────
  async listSourcesOfTruth(page = 1) { return this.request<any>(`/sources-of-truth?page=${page}`); }
  async ingestSourceOfTruth(data: any, apiKey: string) { return this.request<any>('/sources-of-truth/ingest', { method: 'POST', body: JSON.stringify(data) }, apiKey); }

  // ── Risks ───────────────────────────────────────────────────────
  async listRisks(page = 1) { return this.request<any>(`/risks?page=${page}`); }

  // ── Correlations ────────────────────────────────────────────────
  async listCorrelations(page = 1, includeExcluded = false) {
    return this.request<any>(`/correlations?page=${page}&include_excluded=${includeExcluded}`);
  }
  async excludeCorrelation(id: string, reason: string) {
    return this.request<any>(`/correlations/${id}/exclude`, { method: 'PATCH', body: JSON.stringify({ reason }) });
  }
  async includeCorrelation(id: string) {
    return this.request<any>(`/correlations/${id}/include`, { method: 'PATCH' });
  }

  // ── Dispatch ────────────────────────────────────────────────────
  async listDispatchEvents(page = 1) { return this.request<any>(`/dispatch/events?page=${page}`); }

  // ── Monitoring ──────────────────────────────────────────────────
  async getLLMUsageSummary(days = 30) { return this.request<any>(`/monitoring/llm-usage/summary?days=${days}`); }

  // ── Admin ───────────────────────────────────────────────────────
  async runTestPipeline(tenantId: string, orgId: string) {
    return this.request<any>(`/admin/test/run-pipeline?tenant_id=${tenantId}&org_id=${orgId}`, { method: 'POST' });
  }
  async purgeTestData(confirm = true, tenantId?: string) {
    const params = new URLSearchParams({ confirm: String(confirm) });
    if (tenantId) params.set('tenant_id', tenantId);
    return this.request<any>(`/admin/test/purge?${params}`, { method: 'POST' });
  }

  // ── SSE ─────────────────────────────────────────────────────────
  async createEventSource(): Promise<EventSource | null> {
    if (!this.accessToken) return null;
    const { ticket } = await this.request<{ ticket: string }>('/dispatch/stream/ticket', { method: 'POST' });
    return new EventSource(`${API_URL}/dispatch/stream?ticket=${encodeURIComponent(ticket)}`);
  }

  // ── Integrations (PR #31) ───────────────────────────────────────
  async listIntegrations(): Promise<IntegrationListResponse> {
    return this.request<IntegrationListResponse>('/integrations');
  }

  async getIntegration(id: string): Promise<IntegrationCardResponse> {
    return this.request<IntegrationCardResponse>(`/integrations/${id}`);
  }

  async updateIntegration(
    id: string,
    payload: {
      display_name?: string;
      config_json?: Record<string, unknown>;
      is_active?: boolean;
    },
  ): Promise<IntegrationCardResponse> {
    return this.request<IntegrationCardResponse>(`/integrations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async disconnectIntegration(
    id: string,
  ): Promise<{ integration_id: string; status: string }> {
    return this.request<{ integration_id: string; status: string }>(
      `/integrations/${id}`,
      { method: 'DELETE' },
    );
  }

  /**
   * Start the OAuth install flow for the given provider. Returns the
   * authorize URL — caller opens it in a new tab or redirects.
   */
  async startIntegrationInstall(
    provider: IntegrationProvider,
  ): Promise<InstallStartResponse> {
    return this.request<InstallStartResponse>(
      `/integrations/${provider}/install`,
    );
  }

  // ── Ardoq AI-Lens export (#248) ──────────────────────────────────
  async getArdoqManifest(): Promise<ArdoqManifestResponse> {
    return this.request<ArdoqManifestResponse>('/integrations/ardoq/manifest');
  }

  async downloadArdoqExport(): Promise<Blob> {
    return this.requestBlob('/integrations/ardoq/export');
  }

  // ── MDM connect endpoints (Jamf / Kandji / JumpCloud) ───────────
  // Non-OAuth MDMs use these dedicated endpoints instead of /install.
  // Credentials get Fernet-encrypted server-side; the cleartext
  // password / token never round-trips back via the API.

  async jamfConnect(payload: {
    server_url: string;
    username: string;
    password: string;
  }): Promise<IntegrationCardResponse> {
    return this.request<IntegrationCardResponse>('/integrations/jamf/connect', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async kandjiConnect(payload: {
    base_url: string;
    api_token: string;
  }): Promise<IntegrationCardResponse> {
    return this.request<IntegrationCardResponse>(
      '/integrations/kandji/connect',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  }

  async jumpcloudConnect(payload: {
    api_key: string;
  }): Promise<IntegrationCardResponse> {
    return this.request<IntegrationCardResponse>(
      '/integrations/jumpcloud/connect',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  }

  // ── Onboarding (PR #33) ─────────────────────────────────────────
  async getOnboardingStatus(): Promise<OnboardingStatusResponse> {
    return this.request<OnboardingStatusResponse>('/onboarding/status');
  }

  async completeOnboarding(
    steps_completed?: string[],
  ): Promise<OnboardingStatusResponse> {
    return this.request<OnboardingStatusResponse>('/onboarding/complete', {
      method: 'POST',
      body: JSON.stringify({ steps_completed: steps_completed ?? [] }),
    });
  }

  async resetOnboarding(): Promise<OnboardingStatusResponse> {
    return this.request<OnboardingStatusResponse>('/onboarding/reset', {
      method: 'POST',
    });
  }

  // ── Use-case registry (PR #26) ──────────────────────────────────
  async listUseCases(params?: {
    page?: number;
    page_size?: number;
    status?: UseCaseStatus;
    source?: UseCaseSource;
    risk_tier?: UseCaseRiskTier;
    tool?: string;
    department?: string;
  }): Promise<UseCaseListResponse> {
    const q = new URLSearchParams();
    if (params?.page) q.set('page', String(params.page));
    if (params?.page_size) q.set('page_size', String(params.page_size));
    if (params?.status) q.set('status', params.status);
    if (params?.source) q.set('source', params.source);
    if (params?.risk_tier) q.set('risk_tier', params.risk_tier);
    if (params?.tool) q.set('tool', params.tool);
    if (params?.department) q.set('department', params.department);
    const qs = q.toString();
    return this.request<UseCaseListResponse>(
      `/use-cases${qs ? `?${qs}` : ''}`,
    );
  }

  async getUseCaseCounts(): Promise<UseCaseCounts> {
    return this.request<UseCaseCounts>('/use-cases/counts');
  }

  async getUseCase(id: string): Promise<UseCaseResponse> {
    return this.request<UseCaseResponse>(`/use-cases/${id}`);
  }

  async createUseCase(payload: {
    title: string;
    tool: string;
    department: string;
    source: UseCaseSource;
    owner_user_id?: string | null;
    status?: UseCaseStatus;
    risk_tier?: UseCaseRiskTier;
    data_classes?: string[];
    frequency?: string | null;
    notes?: string | null;
    dispatched_from_response_id?: string | null;
  }): Promise<UseCaseResponse> {
    return this.request<UseCaseResponse>('/use-cases', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateUseCase(
    id: string,
    payload: Partial<{
      title: string;
      tool: string;
      department: string;
      owner_user_id: string | null;
      status: UseCaseStatus;
      risk_tier: UseCaseRiskTier;
      data_classes: string[];
      frequency: string | null;
      notes: string | null;
    }>,
  ): Promise<UseCaseResponse> {
    return this.request<UseCaseResponse>(`/use-cases/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async deleteUseCase(id: string): Promise<void> {
    await this.request<unknown>(`/use-cases/${id}`, { method: 'DELETE' });
  }

  // ── Owner inference (#244) ───────────────────────────────────────
  async getOwnerSuggestions(params?: {
    limit?: number;
  }): Promise<OwnerSuggestionsListResponse> {
    const q = new URLSearchParams();
    if (params?.limit) q.set('limit', String(params.limit));
    const qs = q.toString();
    return this.request<OwnerSuggestionsListResponse>(
      `/use-cases/owner-suggestions${qs ? `?${qs}` : ''}`,
    );
  }

  // ── AI inventory aggregate + bulk-import ─────────────────────────
  // Backed by /api/v1/ai-inventory router (separate from /use-cases
  // because file-upload + multi-dim grouping have different shapes
  // than single-row CRUD).
  async getAiInventoryAggregate(): Promise<AiInventoryAggregate> {
    return this.request<AiInventoryAggregate>('/ai-inventory/aggregate');
  }

  /** Returns the URL to download the CSV import template. */
  aiInventoryImportTemplateUrl(): string {
    return `${API_URL}/ai-inventory/import-template`;
  }

  /** POST a CSV file to bulk-create UseCase rows. */
  async importAiInventoryCsv(file: File): Promise<AiInventoryImportResponse> {
    const formData = new FormData();
    formData.append('file', file);
    // Can't go through request() — that always sets JSON Content-Type
    // and stringifies the body. FormData needs the browser to set its
    // own multipart boundary, so we use fetch directly here.
    const headers: Record<string, string> = {};
    if (this.accessToken) {
      headers['Authorization'] = `Bearer ${this.accessToken}`;
    }
    const response = await fetch(`${API_URL}/ai-inventory/import`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      throw await this.parseError(response);
    }
    return response.json();
  }

  // ── Surveys (PR #29 / #30) ──────────────────────────────────────
  async listSurveyTemplates(): Promise<{
    templates: SurveyTemplateResponse[];
    total: number;
  }> {
    return this.request('/surveys/templates');
  }

  async seedDefaultSurveyTemplate(): Promise<SurveyTemplateResponse> {
    return this.request<SurveyTemplateResponse>(
      '/surveys/templates/seed-defaults',
      { method: 'POST' },
    );
  }

  async dispatchSurvey(payload: DispatchCreate): Promise<DispatchResponse> {
    return this.request<DispatchResponse>('/surveys/dispatches', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async listSurveyDispatches(
    page = 1,
    pageSize = 50,
  ): Promise<DispatchListResponse> {
    const q = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    return this.request<DispatchListResponse>(`/surveys/dispatches?${q}`);
  }

  async getSurveyDispatch(id: string): Promise<DispatchResponse> {
    return this.request<DispatchResponse>(`/surveys/dispatches/${id}`);
  }

  async getNextSurveyQuestion(
    responseId: string,
  ): Promise<NextQuestionResponse> {
    return this.request<NextQuestionResponse>(
      `/surveys/responses/${responseId}/next`,
    );
  }

  async submitSurveyAnswer(
    responseId: string,
    payload: { question_id: string; value: unknown },
  ): Promise<NextQuestionResponse> {
    return this.request<NextQuestionResponse>(
      `/surveys/responses/${responseId}/answer`,
      { method: 'POST', body: JSON.stringify(payload) },
    );
  }

  async deleteSurveyResponse(responseId: string): Promise<void> {
    await this.request<unknown>(`/surveys/responses/${responseId}`, {
      method: 'DELETE',
    });
  }

  // ── Endpoint compliance (cross-MDM aggregate) ───────────────────
  async listEndpointCompliance(): Promise<EndpointComplianceResponse> {
    return this.request<EndpointComplianceResponse>('/endpoints/compliance');
  }

  async listEndpointDevices(
    provider: IntegrationProvider,
    page = 1,
    pageSize = 100,
  ): Promise<EndpointDevicesResponse> {
    const q = new URLSearchParams({
      provider,
      page: String(page),
      page_size: String(pageSize),
    });
    return this.request<EndpointDevicesResponse>(`/endpoints/devices?${q}`);
  }

  // ── Handbook (training & awareness — Øystein priority #1) ───────
  async getHandbookStatus(): Promise<HandbookStatusResponse> {
    return this.request<HandbookStatusResponse>('/handbook/status');
  }

  async acknowledgeHandbook(notes?: string): Promise<HandbookAckResponse> {
    return this.request<HandbookAckResponse>('/handbook/acknowledge', {
      method: 'POST',
      body: JSON.stringify({ notes: notes ?? null }),
    });
  }

  async getHandbookTenantStats(): Promise<HandbookTenantStats> {
    return this.request<HandbookTenantStats>('/handbook/tenant-stats');
  }

  /** Returns the tenant's handbook content + version, or { is_stock: true,
   *  content_markdown: null } when no override exists — frontend should
   *  fall back to its curated stock Markdown blocks in that case. */
  async getHandbookContent(): Promise<HandbookContentResponse> {
    return this.request<HandbookContentResponse>('/handbook/content');
  }

  /** OrgAdmin+ — upsert the tenant's handbook override.
   *  Bumping `version` invalidates existing acknowledgements and
   *  triggers re-prompts. */
  async upsertHandbookOverride(
    payload: HandbookOverrideUpsert,
  ): Promise<HandbookContentResponse> {
    return this.request<HandbookContentResponse>('/handbook/content', {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  /** OrgAdmin+ — revert to stock content + version. */
  async revertHandbookOverride(): Promise<void> {
    await this.request<unknown>('/handbook/content', { method: 'DELETE' });
  }

  // ── Use case re-attestation (Øystein priority #3) ───────────────
  async listUseCaseReviews(params?: {
    status?: ReviewStatusServer;
    /** When true, returns only reviews for use cases the caller owns. */
    mine?: boolean;
    page?: number;
    page_size?: number;
  }): Promise<UseCaseReviewListResponse> {
    const q = new URLSearchParams();
    if (params?.status) q.set('status', params.status);
    if (params?.mine) q.set('mine', 'true');
    if (params?.page) q.set('page', String(params.page));
    if (params?.page_size) q.set('page_size', String(params.page_size));
    const qs = q.toString();
    return this.request<UseCaseReviewListResponse>(
      `/use-case-reviews${qs ? `?${qs}` : ''}`,
    );
  }

  async getUseCaseReviewCounts(): Promise<UseCaseReviewCounts> {
    return this.request<UseCaseReviewCounts>('/use-case-reviews/counts');
  }

  async markUseCaseReviewed(
    reviewId: string,
    payload: { notes?: string; drift_observed?: boolean } = {},
  ): Promise<UseCaseReviewResponse> {
    return this.request<UseCaseReviewResponse>(
      `/use-case-reviews/${reviewId}/mark-reviewed`,
      {
        method: 'POST',
        body: JSON.stringify({
          notes: payload.notes ?? null,
          drift_observed: payload.drift_observed ?? false,
        }),
      },
    );
  }

  async dismissUseCaseReview(
    reviewId: string,
    reason: string,
  ): Promise<UseCaseReviewResponse> {
    return this.request<UseCaseReviewResponse>(
      `/use-case-reviews/${reviewId}/dismiss`,
      {
        method: 'POST',
        body: JSON.stringify({ reason }),
      },
    );
  }
}

export const api = new ApiClient();
