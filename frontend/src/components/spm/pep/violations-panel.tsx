// ─────────────────────────────────────────────────────────────────────
// ViolationsPanel — 30-day hit volume across policy instances, plus the
// most recent promotion/demotion events.
//
// GAP: the backend (#237/#238) writes individual `PolicyViolation` rows
// on every blocked evaluation, but exposes no `GET /pep/violations` list
// endpoint yet — only the rolling `stats` counters on each
// `PolicyInstance` (block_count_30d / flag_count_30d). So "top policies"
// below is real, aggregate data; a per-event violation feed and
// per-application breakdown aren't available until that endpoint ships.
// "Recent events" instead surfaces `promotion_history`, which the API
// does return.
// ─────────────────────────────────────────────────────────────────────

import type { PolicyInstance } from '@/lib/aispm/pep';

interface ViolationsPanelProps {
  policies: PolicyInstance[];
}

function StackedHitBar({ block, flag }: { block: number; flag: number }) {
  const total = block + flag;
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0);
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-gray-100">
      <div className="h-full bg-red-500" style={{ width: `${pct(block)}%` }} />
      <div className="h-full bg-amber-400" style={{ width: `${pct(flag)}%` }} />
    </div>
  );
}

export function ViolationsPanel({ policies }: ViolationsPanelProps) {
  const topPolicies = [...policies]
    .map((p) => ({
      instance: p,
      block: p.stats.block_count_30d ?? 0,
      flag: p.stats.flag_count_30d ?? 0,
    }))
    .filter((p) => p.block + p.flag > 0)
    .sort((a, b) => b.block + b.flag - (a.block + a.flag))
    .slice(0, 5);

  const recentEvents = policies
    .flatMap((p) => p.promotion_history.map((e) => ({ instance: p, event: e })))
    .sort((a, b) => (a.event.at < b.event.at ? 1 : -1))
    .slice(0, 8);

  return (
    <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <h2 className="text-sm font-semibold text-gray-900">Violations (30d)</h2>
      <p className="mt-0.5 text-xs text-gray-500">
        Block (red) vs flag (amber) hit volume per policy, from each instance&apos;s rolling stats.
      </p>

      <div className="mt-4 space-y-3">
        {topPolicies.length === 0 && (
          <p className="text-xs text-gray-500">No hits recorded in the last 30 days.</p>
        )}
        {topPolicies.map(({ instance, block, flag }) => (
          <div key={instance.id}>
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-gray-800">{instance.name}</span>
              <span className="tabular-nums text-gray-500">
                {block} block · {flag} flag
              </span>
            </div>
            <div className="mt-1">
              <StackedHitBar block={block} flag={flag} />
            </div>
          </div>
        ))}
      </div>

      <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-gray-500">
        Recent events
      </h3>
      <ul className="mt-2 space-y-1.5">
        {recentEvents.length === 0 && (
          <li className="text-xs text-gray-500">No promotion or demotion events yet.</li>
        )}
        {recentEvents.map(({ instance, event }, i) => (
          <li key={`${instance.id}-${i}`} className="text-xs text-gray-600">
            <span className="font-medium text-gray-800">{instance.name}</span>{' '}
            {event.from} → {event.to} by {event.by}
            <span className="text-gray-400"> · {new Date(event.at).toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
