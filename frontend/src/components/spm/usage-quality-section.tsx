// ─────────────────────────────────────────────────────────────────────
// UsageQualitySection — ROI/upskilling headline + 4-dimension quality
// scorecard for the Adoption page (issue #249, M6).
//
// Dumb presentational component — the page fetches the scorecard from
// GET /adoption/usage-quality (lib/aispm/adoption.ts) and hands it down.
// No chart lib: hand-rolled score bars, matching the rest of the dashboard.
// ─────────────────────────────────────────────────────────────────────

import type { QualityDimension, UsageQualityScorecard } from '@/lib/aispm/adoption';

function TrendBadge({ trend }: { trend: number }) {
  if (trend === 0) {
    return <span className="text-xs font-medium text-gray-400">flat</span>;
  }
  const positive = trend > 0;
  return (
    <span className={`text-xs font-medium ${positive ? 'text-emerald-600' : 'text-rose-600'}`}>
      {positive ? '+' : ''}
      {trend.toFixed(1)} pts
    </span>
  );
}

function DimensionCard({ dimension }: { dimension: QualityDimension }) {
  const width = Math.max(0, Math.min(100, dimension.score));
  return (
    <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          {dimension.label}
        </span>
        <TrendBadge trend={dimension.trend} />
      </div>
      <p className="mt-2 text-2xl font-bold text-gray-900">{dimension.score}</p>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-gray-100">
        <div className="h-2 rounded-full bg-indigo-500" style={{ width: `${width}%` }} />
      </div>
      <p className="mt-3 text-xs text-gray-500">{dimension.narrative}</p>
    </div>
  );
}

interface UsageQualitySectionProps {
  scorecard: UsageQualityScorecard;
}

export function UsageQualitySection({ scorecard }: UsageQualitySectionProps) {
  const { headline, dimensions } = scorecard;
  return (
    <section className="space-y-4">
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">Usage quality</h2>
        <p className="mt-1 text-xs text-gray-500">
          ROI and upskilling from AI-assisted work over the last {headline.window_days} days.
        </p>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-gray-500">ROI</p>
            <p className="mt-1 text-3xl font-bold text-gray-900">
              {headline.roi_multiplier.toFixed(1)}x
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
              Hours saved / week
            </p>
            <p className="mt-1 text-3xl font-bold text-gray-900">
              {headline.hours_saved_per_week.toFixed(1)}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
              Upskilling
            </p>
            <p className="mt-1 text-3xl font-bold text-gray-900">{headline.upskilling_score}</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {dimensions.map((d) => (
          <DimensionCard key={d.key} dimension={d} />
        ))}
      </div>
    </section>
  );
}
