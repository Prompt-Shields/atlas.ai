// ─────────────────────────────────────────────────────────────────────
// ComplianceCoverageCard — framework coverage tile for /dashboard/comply.
//
// Ported from ai-spm-dashboard/components/compliance-coverage-card.tsx.
// Shows a framework's name, headline coverage % (in the framework's own
// colour), a stacked covered/partial/gap bar (via the shared CoverageBar),
// and a gaps label supplied by the caller.
//
// The source rendered a single-colour percentage bar; here we use the
// shared CoverageBar so the segment breakdown is visible and consistent
// with the /comply coverage breakdown.
// ─────────────────────────────────────────────────────────────────────

import { CoverageBar } from '@/components/spm/coverage-bar';

export interface FrameworkCoverage {
  name: string;
  percentage: number;
  gapCount: number;
  color: string;
  covered: number;
  partial: number;
  gap: number;
  total: number;
}

interface ComplianceCoverageCardProps {
  framework: FrameworkCoverage;
  gapsLabel: string;
}

export function ComplianceCoverageCard({ framework, gapsLabel }: ComplianceCoverageCardProps) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-800">{framework.name}</span>
        <span className="text-lg font-bold" style={{ color: framework.color }}>
          {framework.percentage}%
        </span>
      </div>
      <div className="mb-2">
        <CoverageBar
          covered={framework.covered}
          partial={framework.partial}
          gap={framework.gap}
          total={framework.total}
        />
      </div>
      <div className="text-xs text-slate-500">{gapsLabel}</div>
    </div>
  );
}
