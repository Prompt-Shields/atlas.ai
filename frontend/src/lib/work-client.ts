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
 * GET /billing/guided-steps (promptly-guide #62): the Work client's pool, the hard cap,
 * and the hosted guided steps of months that have closed. Step counts only; the
 * backend sends no money. The open month's count is never sent (promptly-guide
 * decision P9): in a small organisation it would show one person's use.
 */
export interface ClosedStepMonth {
  /** The UTC calendar month, "YYYY-MM". */
  period: string;
  /** Hosted steps served that month, for the whole organisation. */
  used: number;
}

export interface GuidedSteps {
  /** The open UTC calendar month, "YYYY-MM". Its count is shown once it ends. */
  period: string;
  /**
   * Steps the Work client includes each month, at today's seats. Null while Atlas has
   * no pool size configured; no hosted step is served until it has.
   */
  pool: number | null;
  /** On: hosted steps stop at the pool until the month turns. Off by default. */
  hard_cap: boolean;
  own_endpoint: boolean;
  work_client: boolean;
  /** Newest first; months with no hosted steps are left out. */
  closed_months: ClosedStepMonth[];
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
