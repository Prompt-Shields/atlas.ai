'use client';

// ─────────────────────────────────────────────────────────────────────
// Activity Log — atlas dashboard §3.2
//
// Purpose:
//   The click-around page. Single most useful surface for an IT lead.
//   Renders every PolicyViolation-style event from the curated feed
//   as a filterable + searchable table.
//
// Filters (5 in a responsive grid):
//   Department · AI tool · Event type · Severity · Search
//
// URL params (deep-link friendly):
//   ?severity=High        →  pre-filters severity (used by Overview's
//                            high-severity hero tile)
//   ?event=Redacted       →  pre-filters event type
//   ?tool=Copilot         →  pre-filters AI tool
//   ?dept=Finance         →  pre-filters department (URL-decoded
//                            from the dept name)
//   ?q=ssn                →  text search across user / detail /
//                            sensitive type
//   ?source=purview       →  renders a "wiring-in-progress" callout;
//                            Purview events surface here once the
//                            tenant-audit pull lands (see consolidation
//                            doc §3.2 source-merge follow-up).
//
// Event pill colours (semantic, do NOT theme):
//   Redacted    → emerald
//   Anonymised  → blue
//   Blocked     → red
//   Coached     → purple
//   Bias-flagged→ orange
//
// Source:
//   frontend/src/lib/curated-demo-data.ts → ACTIVITY_EVENTS.
//   When cursor's M2 routers ship, swap the import for
//   `await api.listActivityEvents({ filters })` in a server
//   component. The pipeline below is a pure function of the input
//   array, so the swap doesn't change the JSX.
//
// This page replaces the older live-dispatch feed (`DispatchEvent`
// JSON dump). The dispatch surface was an internal debugging view
// that pilot feedback called out as developer-only. Activity Log is the
// customer-facing replacement.
// ─────────────────────────────────────────────────────────────────────

import { useMemo, useState, useEffect, useCallback, Suspense } from 'react';
import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import Link from 'next/link';
import {
  DEMO_DATA_BANNER,
  ACTIVITY_EVENTS,
  DEPARTMENTS,
  ORG,
  type ActivityEvent,
  type ActivityEventType,
  type ActivitySeverity,
} from '@/lib/curated-demo-data';
import { isDemoFallbackEnabled } from '@/lib/demo-mode';

// ─── Constants ────────────────────────────────────────────────────────

const EVENT_TYPES: ActivityEventType[] = [
  'Redacted',
  'Anonymised',
  'Blocked',
  'Coached',
  'Bias-flagged',
];

const SEVERITIES: ActivitySeverity[] = ['Low', 'Medium', 'High'];

// Tool list — derived from the data so we don't drift if seeds change.
function uniqueTools(events: ActivityEvent[]): string[] {
  const set = new Set<string>();
  for (const e of events) set.add(e.tool);
  return Array.from(set).sort();
}

// ─── Pills ────────────────────────────────────────────────────────────

const EVENT_PILL_CLASSES: Record<ActivityEventType, string> = {
  Redacted: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  Anonymised: 'bg-blue-50 text-blue-700 ring-blue-200',
  Blocked: 'bg-red-50 text-red-700 ring-red-200',
  Coached: 'bg-purple-50 text-purple-700 ring-purple-200',
  'Bias-flagged': 'bg-orange-50 text-orange-700 ring-orange-200',
};

function EventPill({ type }: { type: ActivityEventType }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${EVENT_PILL_CLASSES[type]}`}
    >
      {type}
    </span>
  );
}

const SEVERITY_CHIP_CLASSES: Record<ActivitySeverity, string> = {
  Low: 'bg-gray-100 text-gray-700',
  Medium: 'bg-amber-50 text-amber-700',
  High: 'bg-red-50 text-red-700',
};

function SeverityChip({ severity }: { severity: ActivitySeverity }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${SEVERITY_CHIP_CLASSES[severity]}`}
    >
      {severity}
    </span>
  );
}

// ─── Filters ──────────────────────────────────────────────────────────

interface Filters {
  dept: string; // '' = all
  tool: string;
  event: string;
  severity: string;
  q: string;
}

const EMPTY_FILTERS: Filters = {
  dept: '',
  tool: '',
  event: '',
  severity: '',
  q: '',
};

function readFiltersFromUrl(params: URLSearchParams): Filters {
  return {
    dept: params.get('dept') || '',
    tool: params.get('tool') || '',
    event: params.get('event') || '',
    severity: params.get('severity') || '',
    q: params.get('q') || '',
  };
}

function buildQueryString(f: Filters): string {
  const usp = new URLSearchParams();
  if (f.dept) usp.set('dept', f.dept);
  if (f.tool) usp.set('tool', f.tool);
  if (f.event) usp.set('event', f.event);
  if (f.severity) usp.set('severity', f.severity);
  if (f.q) usp.set('q', f.q);
  const s = usp.toString();
  return s ? `?${s}` : '';
}

function applyFilters(events: ActivityEvent[], f: Filters): ActivityEvent[] {
  const q = f.q.trim().toLowerCase();
  return events.filter((e) => {
    if (f.dept && e.department !== f.dept) return false;
    if (f.tool && e.tool !== f.tool) return false;
    if (f.event && e.eventType !== f.event) return false;
    if (f.severity && e.severity !== f.severity) return false;
    if (q) {
      const hay =
        `${e.user} ${e.detail} ${e.sensitiveType} ${e.tool} ${e.department}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

// ─── Inputs ───────────────────────────────────────────────────────────

function Select({
  label,
  value,
  options,
  onChange,
  placeholder = 'All',
}: {
  label: string;
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

function SearchInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
        Search
      </span>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="User, detail, sensitive type…"
        className="rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
      />
    </label>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────
//
// Next.js 15+ requires useSearchParams() consumers to live inside a
// Suspense boundary so static rendering can defer the search-param
// read. The default export wraps the real component to provide that
// boundary cleanly.

export default function FeedPage() {
  if (!isDemoFallbackEnabled()) {
    return (
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h1 className="text-2xl font-bold text-gray-900">Activity Log</h1>
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
    <Suspense fallback={<ActivityLogSkeleton />}>
      <ActivityLog />
    </Suspense>
  );
}

function ActivityLogSkeleton() {
  return (
    <div>
      <div className="h-8 w-40 animate-pulse rounded bg-gray-100" />
      <div className="mt-4 h-4 w-72 animate-pulse rounded bg-gray-100" />
      <div className="mt-6 h-32 animate-pulse rounded-xl bg-gray-50 ring-1 ring-gray-100" />
      <div className="mt-6 h-96 animate-pulse rounded-xl bg-gray-50 ring-1 ring-gray-100" />
    </div>
  );
}

function ActivityLog() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);

  // Hydrate from URL on first render + whenever params change (e.g.
  // user clicks browser back).
  useEffect(() => {
    const usp = new URLSearchParams(searchParams.toString());
    setFilters(readFiltersFromUrl(usp));
  }, [searchParams]);

  // Push filter state into the URL so deep links work + browser back
  // restores the prior filter set.
  const updateFilter = useCallback(
    (next: Filters) => {
      setFilters(next);
      router.replace(`${pathname}${buildQueryString(next)}`, {
        scroll: false,
      });
    },
    [pathname, router],
  );

  const setOne = (key: keyof Filters) => (v: string) =>
    updateFilter({ ...filters, [key]: v });

  const reset = () => updateFilter(EMPTY_FILTERS);

  const tools = useMemo(() => uniqueTools(ACTIVITY_EVENTS), []);
  const deptNames = useMemo(() => DEPARTMENTS.map((d) => d.name), []);

  const filtered = useMemo(
    () => applyFilters(ACTIVITY_EVENTS, filters),
    [filters],
  );

  const showPurviewCallout = searchParams.get('source') === 'purview';

  const anyFilter =
    filters.dept ||
    filters.tool ||
    filters.event ||
    filters.severity ||
    filters.q;

  return (
    <div>
      <span className="inline-flex rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
        {DEMO_DATA_BANNER}
      </span>

      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Activity Log</h1>
          <p className="mt-1 text-sm text-gray-600">
            {ORG.name} — last {ORG.reportingPeriodDays} days · showing{' '}
            <span className="font-medium text-gray-900">
              {filtered.length}
            </span>{' '}
            of {ACTIVITY_EVENTS.length} events
          </p>
        </div>
        {anyFilter && (
          <button
            type="button"
            onClick={reset}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            Reset filters
          </button>
        )}
      </div>

      {/* Purview wiring callout */}
      {showPurviewCallout && (
        <div className="mt-4 rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-900 ring-1 ring-blue-100">
          <strong>Purview events not yet wired.</strong> Microsoft Purview
          DLP events will surface in this log once the tenant-audit pull
          lands (consolidation doc §3.2 follow-up). For now this page
          shows Promptly-agent events only.
        </div>
      )}

      {/* Filters */}
      <div className="mt-6 grid grid-cols-1 gap-3 rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-200 sm:grid-cols-2 lg:grid-cols-5">
        <Select
          label="Department"
          value={filters.dept}
          options={deptNames}
          onChange={setOne('dept')}
        />
        <Select
          label="AI tool"
          value={filters.tool}
          options={tools}
          onChange={setOne('tool')}
        />
        <Select
          label="Event"
          value={filters.event}
          options={EVENT_TYPES}
          onChange={setOne('event')}
        />
        <Select
          label="Severity"
          value={filters.severity}
          options={SEVERITIES}
          onChange={setOne('severity')}
        />
        <SearchInput value={filters.q} onChange={setOne('q')} />
      </div>

      {/* Table */}
      <div className="mt-6 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-100 text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-4 py-2">Timestamp</th>
                <th className="px-4 py-2">User</th>
                <th className="px-4 py-2">Department</th>
                <th className="px-4 py-2">AI tool</th>
                <th className="px-4 py-2">Event</th>
                <th className="px-4 py-2">Sensitive type</th>
                <th className="px-4 py-2">Detail</th>
                <th className="px-4 py-2">Severity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-10 text-center text-sm text-gray-500"
                  >
                    No events match your filters.{' '}
                    <button
                      type="button"
                      onClick={reset}
                      className="font-medium text-primary-600 hover:text-primary-700"
                    >
                      Reset
                    </button>
                  </td>
                </tr>
              )}
              {filtered.map((e) => (
                <tr key={e.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                    {new Date(e.timestamp).toLocaleString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-gray-900">
                    {e.user}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-gray-700">
                    {e.department}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-gray-700">
                    {e.tool}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <EventPill type={e.eventType} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-600">
                    {e.sensitiveType}
                  </td>
                  <td className="px-4 py-3 text-gray-700">
                    <span
                      className="block max-w-md truncate"
                      title={e.detail}
                    >
                      {e.detail}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <SeverityChip severity={e.severity} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Footer cross-link */}
      <p className="mt-4 text-xs text-gray-500">
        Users are tagged{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
          [DEMO]
        </code>{' '}
        — fictional placeholders. The full feed comes from
        <code className="ml-1 rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
          PolicyViolation
        </code>{' '}
        once cursor&apos;s M2 routers ship.{' '}
        <Link
          href="/dashboard/overview"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Back to Overview →
        </Link>
      </p>
    </div>
  );
}
