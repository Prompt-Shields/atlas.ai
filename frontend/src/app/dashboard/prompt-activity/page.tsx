'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  telemetryApi,
  type PromptActivitySummary,
  type PromptActivityBucket,
  type PromptActivityBreakdownRow,
  type PromptEventSource,
} from '@/lib/telemetry';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

type SourceFilter = 'all' | PromptEventSource;
type DateRange = 7 | 30 | 90;

// ──────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────

function sinceDate(days: DateRange): string {
  const now = new Date();
  const since = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - days)).toISOString();
  return since.slice(0, 10);
}

/**
 * Zero-fill gaps between `since` (YYYY-MM-DD) and today.
 *
 * The API buckets dates in UTC, so the walk must also use UTC days —
 * mixing local midnight with toISOString() shifts keys by ±1 day in
 * UTC-offset timezones and silently drops the newest bucket.
 */
function fillTimeseries(
  buckets: PromptActivityBucket[],
  since: string,
): PromptActivityBucket[] {
  const byDate = new Map(buckets.map((b) => [b.date, b]));
  const result: PromptActivityBucket[] = [];

  // Parse since as a UTC date (YYYY-MM-DD).
  const [sy, sm, sd] = since.split('-').map(Number);
  const startMs = Date.UTC(sy, sm - 1, sd);

  // End = today's UTC date (midnight UTC).
  const now = new Date();
  const endMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());

  const cur = new Date(startMs);
  while (cur.getTime() <= endMs) {
    const key = cur.toISOString().slice(0, 10); // always YYYY-MM-DD UTC
    result.push(byDate.get(key) ?? { date: key, prompts: 0, violations: 0 });
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return result;
}

// ──────────────────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────────────────

export default function PromptActivityPage() {
  const [source, setSource] = useState<SourceFilter>('all');
  const [days, setDays] = useState<DateRange>(30);

  const [summary, setSummary] = useState<PromptActivitySummary | null>(null);
  const [timeseries, setTimeseries] = useState<PromptActivityBucket[]>([]);
  const [appRows, setAppRows] = useState<PromptActivityBreakdownRow[]>([]);
  const [piiRows, setPiiRows] = useState<PromptActivityBreakdownRow[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const since = sinceDate(days);
  const filters = {
    since,
    source: source === 'all' ? undefined : source,
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      telemetryApi.summary(filters),
      telemetryApi.timeseries(filters),
      telemetryApi.breakdown('app', filters),
      telemetryApi.breakdown('pii_category', filters),
    ])
      .then(([s, ts, app, pii]) => {
        if (cancelled) return;
        setSummary(s);
        setTimeseries(fillTimeseries(ts.buckets, since));
        setAppRows(app.rows);
        setPiiRows(pii.rows);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, days]);

  return (
    <div className="p-8 max-w-7xl">
      <h1 className="text-3xl font-semibold mb-2">Prompt Activity</h1>
      <p className="text-gray-600 mb-6">
        Monitor AI prompt volume, policy violations, and PII exposure across all
        connected clients.
      </p>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-4 mb-6">
        {/* Source tabs */}
        <div className="border-b border-gray-200 flex gap-5">
          {(
            [
              { value: 'all', label: 'All' },
              { value: 'safari_extension', label: 'Safari' },
              { value: 'macos_widget', label: 'macOS' },
              { value: 'sdk', label: 'SDK' },
            ] as { value: SourceFilter; label: string }[]
          ).map((opt) => (
            <button
              key={opt.value}
              onClick={() => setSource(opt.value)}
              aria-pressed={source === opt.value}
              className={`pb-2 px-1 border-b-2 text-sm font-medium ${
                source === opt.value
                  ? 'border-indigo-500 text-indigo-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Date range */}
        <div className="ml-auto flex items-center gap-2">
          <label
            htmlFor="prompt-activity-range"
            className="text-xs font-medium text-gray-500"
          >
            Range
          </label>
          <select
            id="prompt-activity-range"
            value={days}
            onChange={(e) => setDays(Number(e.target.value) as DateRange)}
            className="px-2 py-1 border rounded text-sm"
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>
      </div>

      {error && (
        <p className="text-red-600 mb-4 text-sm">
          Failed to load data: {error}
        </p>
      )}

      {loading && (
        <p className="text-gray-400 animate-pulse mb-4 text-sm">Loading…</p>
      )}

      {/* Empty state */}
      {!loading && !error && summary?.total_prompts === 0 && (
        <div className="border border-gray-200 rounded-lg p-10 text-center">
          <p className="text-2xl mb-3">🔍</p>
          {source === 'all' ? (
            <>
              <p className="font-medium text-gray-700 mb-1">
                No prompt activity yet
              </p>
              <p className="text-sm text-gray-500 mb-4">
                Connect a client (Safari extension, macOS widget, or SDK) to start
                seeing data here.
              </p>
              <Link
                href="/dashboard/developer/settings"
                className="text-sm text-indigo-600 hover:underline"
              >
                Go to Developer Settings → API Keys
              </Link>
            </>
          ) : (
            <p className="text-sm text-gray-500">
              No prompt activity from this source in the selected range.
            </p>
          )}
        </div>
      )}

      {!loading && !error && summary && summary.total_prompts > 0 && (
        <>
          {/* Stat cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <StatCard
              label="Total Prompts"
              value={summary.total_prompts.toLocaleString()}
            />
            <StatCard
              label="Violations"
              value={summary.total_violations.toLocaleString()}
              highlight={summary.total_violations > 0}
            />
            <StatCard
              label="Violation Rate"
              value={`${(summary.violation_rate * 100).toFixed(1)}%`}
              highlight={summary.violation_rate > 0}
            />
            <StatCard
              label="Active Devices"
              value={summary.active_devices.toLocaleString()}
            />
          </div>

          {/* Timeseries chart */}
          <div className="border rounded-lg p-4 mb-8">
            <h2 className="text-sm font-medium text-gray-700 mb-4">
              Daily Prompts &amp; Violations
            </h2>
            <TimeseriesChart buckets={timeseries} />
          </div>

          {/* Breakdown tables */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <BreakdownTable title="By Application" rows={appRows} />
            <BreakdownTable title="By PII Category" rows={piiRows} />
          </div>
        </>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Stat card
// ──────────────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="border border-gray-200 rounded-lg p-4">
      <p className="text-xs font-medium text-gray-500 uppercase mb-1">
        {label}
      </p>
      <p
        className={`text-2xl font-semibold ${
          highlight ? 'text-red-600' : 'text-gray-900'
        }`}
      >
        {value}
      </p>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// CSS bar chart (no external library)
// ──────────────────────────────────────────────────────────────────────

function TimeseriesChart({ buckets }: { buckets: PromptActivityBucket[] }) {
  if (buckets.length === 0) {
    return <p className="text-sm text-gray-400 italic">No data.</p>;
  }

  const maxPrompts = Math.max(...buckets.map((b) => b.prompts), 1);

  const totalPrompts = buckets.reduce((sum, b) => sum + b.prompts, 0);
  const totalViolations = buckets.reduce((sum, b) => sum + b.violations, 0);
  const chartDescription =
    `Bar chart of daily prompts and violations from ${buckets[0].date} ` +
    `to ${buckets[buckets.length - 1].date}: ${totalPrompts.toLocaleString()} ` +
    `prompts and ${totalViolations.toLocaleString()} violations ` +
    `across ${buckets.length} days.`;

  return (
    <div className="overflow-x-auto">
      <div
        role="img"
        aria-label={chartDescription}
        className="flex items-end gap-px min-w-0"
        style={{ height: '120px' }}
      >
        {buckets.map((b) => {
          const promptHeight = Math.round((b.prompts / maxPrompts) * 100);
          const violationHeight =
            b.prompts > 0
              ? Math.round((b.violations / maxPrompts) * 100)
              : 0;
          const label = `${b.date}: ${b.prompts} prompt${b.prompts !== 1 ? 's' : ''}, ${b.violations} violation${b.violations !== 1 ? 's' : ''}`;

          return (
            <div
              key={b.date}
              className="relative flex flex-col justify-end flex-1"
              style={{ height: '100%', minWidth: '4px' }}
              title={label}
            >
              {/* Prompt bar */}
              <div
                className="w-full bg-indigo-200 rounded-t"
                style={{ height: `${promptHeight}%` }}
              />
              {/* Violation overlay — rendered as a subset of the prompts bar
                  on the same scale (not an independent series). */}
              {b.violations > 0 && (
                <div
                  className="absolute bottom-0 left-0 right-0 bg-red-400 rounded-t"
                  style={{ height: `${violationHeight}%`, minHeight: '2px' }}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* X-axis labels: show first, middle, last */}
      {buckets.length >= 2 && (
        <div className="flex justify-between mt-1 text-xs text-gray-400">
          <span>{buckets[0].date}</span>
          {buckets.length > 2 && (
            <span>{buckets[Math.floor(buckets.length / 2)].date}</span>
          )}
          <span>{buckets[buckets.length - 1].date}</span>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded bg-indigo-200" />
          Prompts
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded bg-red-400" />
          Violations
        </span>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Breakdown table
// ──────────────────────────────────────────────────────────────────────

function BreakdownTable({
  title,
  rows,
}: {
  title: string;
  rows: PromptActivityBreakdownRow[];
}) {
  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="bg-gray-50 px-4 py-2 border-b">
        <h2 className="text-sm font-medium text-gray-700">{title}</h2>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-gray-400 italic text-center">
          No data.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left">
            <tr className="border-b">
              <th className="py-2 px-4 text-xs font-medium text-gray-500 uppercase">
                Name
              </th>
              <th className="py-2 px-4 text-xs font-medium text-gray-500 uppercase text-right">
                Prompts
              </th>
              <th className="py-2 px-4 text-xs font-medium text-gray-500 uppercase text-right">
                Violations
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t hover:bg-gray-50">
                <td className="py-2 px-4 font-mono text-xs">
                  {row.key || <span className="text-gray-400 italic">unknown</span>}
                </td>
                <td className="py-2 px-4 text-right">
                  {row.prompts.toLocaleString()}
                </td>
                <td
                  className={`py-2 px-4 text-right ${
                    row.violations > 0 ? 'text-red-600' : 'text-gray-400'
                  }`}
                >
                  {row.violations > 0 ? row.violations.toLocaleString() : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
