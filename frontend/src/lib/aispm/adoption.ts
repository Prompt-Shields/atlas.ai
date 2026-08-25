'use client';

/**
 * Adoption usage-quality scorecard API client and types (issue #249, M6).
 *
 * Mirrors the backend Pydantic schemas in `app/schemas/adoption.py` and the
 * `/api/v1/adoption` router: a ROI/upskilling headline plus four quality
 * dimensions (outcome, leverage, trajectory, diffusion), currently backed
 * by a seeded transcript pipeline server-side — see that router's
 * docstring for the real-telemetry migration path.
 */

import { api } from '../api';

// ──────────────────────────────────────────────────────────────────────
// Types (mirror backend/app/schemas/adoption.py)
// ──────────────────────────────────────────────────────────────────────

export type QualityDimensionKey = 'outcome' | 'leverage' | 'trajectory' | 'diffusion';

export interface QualityDimension {
  key: QualityDimensionKey;
  label: string;
  score: number;
  trend: number;
  narrative: string;
}

export interface UsageQualityHeadline {
  roi_multiplier: number;
  hours_saved_per_week: number;
  upskilling_score: number;
  window_days: number;
}

export interface UsageQualityScorecard {
  headline: UsageQualityHeadline;
  dimensions: QualityDimension[];
}

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/adoption';

export const adoptionApi = {
  usageQuality: (): Promise<UsageQualityScorecard> =>
    api.request<UsageQualityScorecard>(`${BASE}/usage-quality`),
};
