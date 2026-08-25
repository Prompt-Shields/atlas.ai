'use client';

// ─────────────────────────────────────────────────────────────────────
// InventoryAggregateCard — top-of-page stats card for the AI inventory.
//
// Reads from /api/v1/ai-inventory/aggregate. Soft-fails to a hidden
// state if the backend is unreachable — the rest of the page is
// powered by curated demo data and can render fine without us.
//
// The shape mirrors the questions Øystein actually asks when looking
// at the inventory in our customer calls:
//   "How many use cases total?"
//   "How many have no owner?" — the IT lead's homework
//   "How many ACTIVE+HIGH-risk?" — the hot list
//   "Which departments and tools dominate?" — the centres of gravity
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { AiInventoryAggregate } from '@/lib/types';

export function InventoryAggregateCard() {
  const [agg, setAgg] = useState<AiInventoryAggregate | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const a = await api.getAiInventoryAggregate();
        if (!cancelled) setAgg(a);
      } catch {
        // soft fail — the page still works with curated demo data
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Don't render anything until we know whether we have real data —
  // avoids a flash of empty card.
  if (!loaded) return null;
  if (!agg || agg.total === 0) return null;

  const topDept = agg.by_department[0];
  const topTool = agg.by_tool[0];

  return (
    <section
      className="mb-6 rounded-lg border border-slate-200 bg-white p-4"
      aria-label="AI inventory summary"
    >
      <div className="grid gap-4 md:grid-cols-4">
        <Stat label="Registered use cases" value={agg.total} />
        <Stat
          label="Without an owner"
          value={agg.unowned_count}
          warning={agg.unowned_count > 0}
        />
        <Stat
          label="ACTIVE · HIGH risk"
          value={agg.active_high_risk_count}
          warning={agg.active_high_risk_count > 0}
        />
        <Stat
          label="Top tool"
          value={topTool ? topTool.label : '—'}
          subtitle={topTool ? `${topTool.count} use case${topTool.count === 1 ? '' : 's'}` : undefined}
        />
      </div>

      {/* Status + risk distribution rendered as compact pills */}
      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-3 text-xs text-slate-600">
        <span className="font-medium text-slate-700">Status:</span>
        {Object.entries(agg.by_status).map(([k, v]) => (
          <span key={k} className="rounded-full bg-slate-100 px-2 py-0.5">
            {k} <span className="font-semibold text-slate-900">{v}</span>
          </span>
        ))}
        <span className="ml-4 font-medium text-slate-700">Risk:</span>
        {Object.entries(agg.by_risk_tier).map(([k, v]) => (
          <span
            key={k}
            className={`rounded-full px-2 py-0.5 ${
              k === 'HIGH'
                ? 'bg-red-100 text-red-800'
                : k === 'MEDIUM'
                ? 'bg-amber-100 text-amber-800'
                : 'bg-emerald-100 text-emerald-800'
            }`}
          >
            {k} <span className="font-semibold">{v}</span>
          </span>
        ))}
        {topDept && (
          <span className="ml-auto text-slate-500">
            Top department:{' '}
            <span className="font-medium text-slate-700">{topDept.label}</span>{' '}
            ({topDept.count})
          </span>
        )}
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  subtitle,
  warning = false,
}: {
  label: string;
  value: number | string;
  subtitle?: string;
  warning?: boolean;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div
        className={`mt-1 text-2xl font-semibold ${
          warning ? 'text-amber-700' : 'text-slate-900'
        }`}
      >
        {value}
      </div>
      {subtitle && (
        <div className="text-xs text-slate-500">{subtitle}</div>
      )}
    </div>
  );
}
