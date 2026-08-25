'use client';

// ─────────────────────────────────────────────────────────────────────
// Overview — atlas dashboard, Phase B
//
// Purpose:
//   The "front door" for the IT-lead persona. Answers four questions
//   in 30 seconds:
//
//     1. Are the agents online?            → Active users tile
//     2. Is anything getting redacted?     → Redacted-prompts tile
//     3. Anything I need to look at now?   → High-severity tile
//     4. Where is the AI use happening?    → Tool donut + dept bar
//
//   Plus a 5-row "recent redaction events" table so the user can
//   click through to /dashboard/feed for the full activity log.
//
// Source of truth:
//   Every number on this page comes from frontend/src/lib/
//   curated-demo-data.ts. When the M2 routers ship, the loaders in
//   that module become async — the JSX here doesn't change.
//
// Charts:
//   No Recharts — manual SVG. The donut is a single circle with
//   dasharray strokes; the dept bar is plain CSS-width <div>s. This
//   keeps the bundle ~0KB extra and the visuals obviously editable.
//
// Visual language:
//   Mirrors the existing /dashboard cards (rounded-xl, ring-1
//   ring-gray-200, bg-white). No new design tokens.
// ─────────────────────────────────────────────────────────────────────

import Link from 'next/link';
import { AdoptionBarList } from '@/components/spm/adoption-bar-list';
import {
  DEMO_DATA_BANNER,
  ORG,
  STACK_BADGES,
  COUNTS_30D,
  TOOL_USAGE,
  ADOPTION_BY_DEPT,
  recentRedactions,
  activeUserShare,
  type ActivityEvent,
  type ActivitySeverity,
} from '@/lib/curated-demo-data';
import { isDemoFallbackEnabled } from '@/lib/demo-mode';

// ─── Sub-components (inline; small + page-specific) ───────────────────

function HeroTile({
  label,
  value,
  trend,
  tone,
  hint,
  href,
}: {
  label: string;
  value: string;
  trend?: { delta: string; direction: 'up' | 'down'; tone: 'good' | 'bad' };
  tone?: 'good' | 'warn' | 'bad' | 'neutral';
  hint?: string;
  href?: string;
}) {
  const valueClass =
    tone === 'good'
      ? 'text-emerald-600'
      : tone === 'warn'
        ? 'text-amber-600'
        : tone === 'bad'
          ? 'text-red-600'
          : 'text-gray-900';
  const trendClass =
    trend?.tone === 'good' ? 'text-emerald-600' : 'text-red-600';
  const arrow = trend?.direction === 'up' ? '▲' : '▼';
  const inner = (
    <>
      <p className="text-sm font-medium text-gray-500">{label}</p>
      <p className={`mt-2 text-3xl font-bold ${valueClass}`}>{value}</p>
      {trend && (
        <p className={`mt-1 text-xs font-medium ${trendClass}`}>
          {arrow} {trend.delta}
        </p>
      )}
      {hint && <p className="mt-2 text-xs text-gray-500">{hint}</p>}
      {href && (
        <p className="mt-3 text-xs font-medium text-primary-600">
          Review →
        </p>
      )}
    </>
  );
  if (href) {
    return (
      <Link
        href={href}
        className="block rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200 transition hover:ring-primary-300 hover:shadow"
      >
        {inner}
      </Link>
    );
  }
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      {inner}
    </div>
  );
}

function ToolUsageDonut() {
  // Manual SVG donut. Math:
  //   - radius 70, circumference 2π·70 ≈ 439.823
  //   - each slice = stroke-dasharray (slicePct·C, C)
  //   - successive slices use stroke-dashoffset = -accumulated
  // Rotate −90° so the donut starts at 12 o'clock.
  const RADIUS = 70;
  const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
  // Offsets are precomputed rather than accumulated inside the map below:
  // mutating a variable while rendering breaks under the React Compiler
  // (react-hooks/immutability), which may memoise the callback separately.
  const sliceOffsets = TOOL_USAGE.reduce<number[]>((offsets, _slice, i) => {
    const previous =
      i === 0 ? 0 : offsets[i - 1] + (TOOL_USAGE[i - 1].sharePct / 100) * CIRCUMFERENCE;
    offsets.push(previous);
    return offsets;
  }, []);
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">AI tool usage</h3>
        <span className="text-xs text-gray-500">
          last {ORG.reportingPeriodDays} days
        </span>
      </div>
      <div className="mt-4 flex items-center gap-6">
        <svg
          viewBox="0 0 200 200"
          className="h-44 w-44 shrink-0"
          aria-hidden="true"
        >
          {/* Track */}
          <circle
            cx={100}
            cy={100}
            r={RADIUS}
            fill="none"
            stroke="#f3f4f6"
            strokeWidth={28}
          />
          {/* Slices */}
          {TOOL_USAGE.map((slice, i) => {
            const length = (slice.sharePct / 100) * CIRCUMFERENCE;
            const offset = -sliceOffsets[i];
            return (
              <circle
                key={slice.tool}
                cx={100}
                cy={100}
                r={RADIUS}
                fill="none"
                stroke={slice.color}
                strokeWidth={28}
                strokeDasharray={`${length} ${CIRCUMFERENCE}`}
                strokeDashoffset={offset}
                transform="rotate(-90 100 100)"
              />
            );
          })}
          {/* Centre label */}
          <text
            x="100"
            y="96"
            textAnchor="middle"
            className="fill-gray-900"
            style={{ fontSize: 22, fontWeight: 700 }}
          >
            {COUNTS_30D.totalPrompts.toLocaleString()}
          </text>
          <text
            x="100"
            y="116"
            textAnchor="middle"
            className="fill-gray-500"
            style={{ fontSize: 11 }}
          >
            prompts
          </text>
        </svg>
        <ul className="flex-1 space-y-2 text-sm">
          {TOOL_USAGE.map((slice) => (
            <li
              key={slice.tool}
              className="flex items-center justify-between gap-3"
            >
              <span className="flex items-center gap-2">
                <span
                  className="inline-block h-3 w-3 rounded-sm"
                  style={{ backgroundColor: slice.color }}
                  aria-hidden="true"
                />
                <span className="text-gray-700">{slice.tool}</span>
                {slice.isShadow && (
                  <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700">
                    Shadow
                  </span>
                )}
              </span>
              <span className="font-medium text-gray-900">
                {slice.sharePct}%
              </span>
            </li>
          ))}
        </ul>
      </div>
      <p className="mt-4 text-xs text-gray-500">
        &ldquo;Shadow&rdquo; tools are unsanctioned but observed by the
        Promptly agent — coach users toward the approved equivalents.
      </p>
    </div>
  );
}

function AdoptionByDepartment() {
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">
          Adoption by department
        </h3>
        <span className="text-xs text-gray-500">% of dept active</span>
      </div>
      <div className="mt-4">
        <AdoptionBarList rows={ADOPTION_BY_DEPT} />
      </div>
    </div>
  );
}

function severityChip(severity: ActivitySeverity) {
  const map = {
    Low: 'bg-gray-100 text-gray-700',
    Medium: 'bg-amber-50 text-amber-700',
    High: 'bg-red-50 text-red-700',
  } as const;
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${map[severity]}`}
    >
      {severity}
    </span>
  );
}

function RecentEventsTable() {
  const events = recentRedactions(5);
  return (
    <div className="rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
        <h3 className="text-sm font-semibold text-gray-900">
          Recent redactions
        </h3>
        <Link
          href="/dashboard/feed"
          className="text-xs font-medium text-primary-600 hover:text-primary-700"
        >
          View all activity →
        </Link>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-100 text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-6 py-2">When</th>
              <th className="px-6 py-2">User</th>
              <th className="px-6 py-2">Tool</th>
              <th className="px-6 py-2">Detail</th>
              <th className="px-6 py-2">Severity</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {events.map((e: ActivityEvent) => (
              <tr key={e.id} className="hover:bg-gray-50">
                <td className="whitespace-nowrap px-6 py-3 text-xs text-gray-500">
                  {new Date(e.timestamp).toLocaleString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </td>
                <td className="px-6 py-3 text-gray-900">{e.user}</td>
                <td className="px-6 py-3 text-gray-700">{e.tool}</td>
                <td className="px-6 py-3 text-gray-700">
                  <span className="block max-w-md truncate" title={e.detail}>
                    {e.detail}
                  </span>
                  <span className="text-xs text-gray-500">
                    {e.sensitiveType}
                  </span>
                </td>
                <td className="px-6 py-3">{severityChip(e.severity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function OverviewPage() {
  if (!isDemoFallbackEnabled()) {
    return (
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h1 className="text-2xl font-bold text-gray-900">Overview</h1>
        <p className="mt-2 text-sm text-gray-600">
          No live data yet. Connect Promptly or enable demo mode.
        </p>
        <p className="mt-2 text-xs text-gray-500">
          For local demos, set NEXT_PUBLIC_DEMO_MODE=1.
        </p>
      </div>
    );
  }

  return (
    <div>
      <span className="inline-flex rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
        {DEMO_DATA_BANNER}
      </span>

      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Overview</h1>
          <p className="mt-1 text-sm text-gray-600">
            {ORG.name} — last {ORG.reportingPeriodDays} days
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {STACK_BADGES.map((badge) => (
            <span
              key={badge}
              className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700"
            >
              {badge}
            </span>
          ))}
        </div>
      </div>

      {/* Hero tiles */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <HeroTile
          label="Active users"
          value={activeUserShare()}
          tone="good"
          hint={`${Math.round(
            (ORG.activeUsers / ORG.totalUsers) * 100,
          )}% of headcount used the agent in the period.`}
        />
        <HeroTile
          label="Sensitive prompts redacted"
          value={COUNTS_30D.redactedPrompts.toLocaleString()}
          tone="warn"
          trend={{
            delta: `${COUNTS_30D.riskyPromptsReductionPct}% MoM (risky prompts down)`,
            direction: 'down',
            tone: 'good',
          }}
          hint="Coached, not blocked. Counts each redaction event once."
        />
        <HeroTile
          label="High-severity events"
          value={String(COUNTS_30D.highSeverityEvents)}
          tone={COUNTS_30D.highSeverityEvents > 0 ? 'bad' : 'good'}
          hint="Compensation / banking / regulated PII pasted into AI tools."
          href={
            COUNTS_30D.highSeverityEvents > 0
              ? '/dashboard/feed?severity=High'
              : undefined
          }
        />
      </div>

      {/* Donut + dept bar */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ToolUsageDonut />
        <AdoptionByDepartment />
      </div>

      {/* Recent events */}
      <div className="mt-6">
        <RecentEventsTable />
      </div>

      {/* Footer callout */}
      <div className="mt-6 rounded-xl bg-blue-50 p-5 ring-1 ring-blue-100">
        <p className="text-sm text-blue-900">
          <strong>How this fits with your existing stack.</strong> Promptly
          coaches at the moment of typing — Microsoft Purview keeps the
          tenant-side audit trail, and SentinelOne handles endpoint
          posture. The numbers above come from the Promptly agent only;
          Purview DLP events show up under{' '}
          <Link
            href="/dashboard/feed?source=purview"
            className="underline hover:text-blue-700"
          >
            Activity log → Purview
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
