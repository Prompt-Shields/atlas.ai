'use client';

/**
 * The free Work client's 90-day rule (promptly-guide #52).
 *
 * Mirrors the Work client fields of app/schemas/billing.py (EntitlementsResponse,
 * WorkClientUpdate). The backend works the status out from the DB server's clock;
 * this module only reads it, so the console and Atlas never disagree on the day.
 */

import { api } from './api';

export type FreeClientStatus = 'active' | 'grace_ending' | 'endpoint_required';

/** The parts of GET /billing/entitlements this page reads. */
export interface WorkClientEntitlements {
  plan: string;
  modules: string[];
  /** None until an admin records day 0. */
  free_client_status: FreeClientStatus | null;
  work_client_started_at: string | null;
  work_client_days: number | null;
  free_client_grace_ends_at: string | null;
  paid_module_active: boolean;
  own_endpoint_declared: boolean;
}

/** PATCH /billing/work-client. Omitted fields are left alone. */
export interface WorkClientUpdate {
  /** Records day 0 if it is not set. The backend never moves or clears it. */
  started?: true;
  own_endpoint_declared?: boolean;
}

/** What the page shows, worked out from the entitlements alone. */
export type WorkClientView =
  | { kind: 'notStarted' }
  | { kind: 'exempt'; reason: 'paidModule' | 'ownEndpoint'; day: number; endsAt: string }
  | { kind: 'counting'; warning: boolean; day: number; total: number; daysLeft: number; endsAt: string }
  | { kind: 'endpointRequired'; day: number; endsAt: string };

const DAY_MS = 864e5;

export function workClientView(e: WorkClientEntitlements): WorkClientView {
  const started = e.work_client_started_at;
  const endsAt = e.free_client_grace_ends_at;
  if (e.free_client_status === null || !started || !endsAt) return { kind: 'notStarted' };

  const day = e.work_client_days ?? 0;
  if (e.free_client_status === 'endpoint_required') return { kind: 'endpointRequired', day, endsAt };
  // A paid module wins over a declaration, as the backend checks it first.
  if (e.paid_module_active) return { kind: 'exempt', reason: 'paidModule', day, endsAt };
  if (e.own_endpoint_declared) return { kind: 'exempt', reason: 'ownEndpoint', day, endsAt };

  // The length comes from the backend's own dates, so 90 is not repeated here.
  const total = Math.round((Date.parse(endsAt) - Date.parse(started)) / DAY_MS);
  return {
    kind: 'counting',
    warning: e.free_client_status === 'grace_ending',
    day,
    total,
    daysLeft: Math.max(total - day, 0),
    endsAt,
  };
}

/**
 * GET /billing/guided-steps (promptly-guide #62): the month's hosted guided steps
 * against the Work client's pool. Step counts only; the backend sends no money.
 */
export interface GuidedSteps {
  /** The UTC calendar month, "YYYY-MM". */
  period: string;
  used: number;
  pool: number;
  /** Steps left in the pool, never below 0. */
  remaining: number;
  /** Steps above the pool this month; 0 with the organisation's own endpoint. */
  overage: number;
  /** On: hosted steps stop at the pool until the month turns. Off by default. */
  hard_cap: boolean;
  own_endpoint: boolean;
  work_client: boolean;
}

/** The first day of the month after `period`, at 00:00 UTC, when the pool starts again. */
export function poolResetsAt(period: string): Date {
  const [year, month] = period.split('-').map(Number);
  // Date.UTC's month is 0-based, so `month` (1-based) is already the next month.
  return new Date(Date.UTC(year, month, 1));
}

export const workClientApi = {
  entitlements(): Promise<WorkClientEntitlements> {
    return api.request<WorkClientEntitlements>('/billing/entitlements');
  },

  /** Tenant admins only. Returns the updated entitlements. */
  update(body: WorkClientUpdate): Promise<WorkClientEntitlements> {
    return api.request<WorkClientEntitlements>('/billing/work-client', {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  },

  guidedSteps(): Promise<GuidedSteps> {
    return api.request<GuidedSteps>('/billing/guided-steps');
  },

  /** Tenant admins only. Returns the month with the new setting. */
  setHardCap(hardCap: boolean): Promise<GuidedSteps> {
    return api.request<GuidedSteps>('/billing/guided-steps/hard-cap', {
      method: 'PUT',
      body: JSON.stringify({ hard_cap: hardCap }),
    });
  },
};
