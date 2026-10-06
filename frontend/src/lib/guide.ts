'use client';

/**
 * Promptly Guide: the 30-day pilot report (promptly-guide #39).
 *
 * Mirrors app/schemas/guide.py (GuidePilotReportOut). Read-only. Every figure here
 * already passed the backend's gate — teams of ten or more, ranges instead of
 * counts, no totals — and every risk row spans at least `minimum_devices` devices,
 * so the page shows what it is given and computes nothing finer.
 */

export interface GuideFigure {
  team: string;
  category_kind: string;
  category_id: string;
  band: string;
  band_lower: number;
}

export interface GuideRiskRow {
  key: string;
  events: number;
  devices: number;
}

export interface GuidePilotMonth {
  period: string;
  tools: GuideFigure[];
  finished: GuideFigure[];
  not_finished: GuideFigure[];
  topics: GuideFigure[];
  teams_too_small: string[];
  suppressed_categories: Record<string, number>;
}

export interface GuidePilotReport {
  connected_at: string;
  generated_at: string;
  offered_kinds: string[];
  months: GuidePilotMonth[];
  risk: {
    since: string;
    until: string;
    by_category: GuideRiskRow[];
    by_app: GuideRiskRow[];
    by_action: GuideRiskRow[];
    suppressed: Record<string, number>;
    minimum_devices: number;
  };
  minimum_group_size: number;
  notes: string[];
}

/** Why there is no report to show: not connected yet, or not 30 days yet. */
export type PilotReportResult =
  | { kind: 'ready'; report: GuidePilotReport }
  | { kind: 'notConnected' }
  | { kind: 'notReady'; readyOn: string };

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

function headers(): Record<string, string> {
  const token = typeof window === 'undefined' ? null : sessionStorage.getItem('access_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export const guideApi = {
  async pilotReport(): Promise<PilotReportResult> {
    const res = await fetch(`${API_URL}/guide/pilot-report`, { headers: headers() });
    if (res.status === 404) return { kind: 'notConnected' };
    if (res.status === 409) {
      const body = (await res.json().catch(() => ({}))) as {
        error?: { details?: { ready_on?: string } };
      };
      return { kind: 'notReady', readyOn: body.error?.details?.ready_on ?? '' };
    }
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return { kind: 'ready', report: (await res.json()) as GuidePilotReport };
  },

  /** The same report as markdown, for sharing. */
  async pilotReportMarkdown(): Promise<string> {
    const res = await fetch(`${API_URL}/guide/pilot-report?format=markdown`, {
      headers: headers(),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.text();
  },
};
