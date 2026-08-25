'use client';

// ─────────────────────────────────────────────────────────────────────
// Audit & Report — atlas dashboard §3.12
//
// Role:
//   The export the IT lead takes upstairs. PDF one-pager that
//   answers "what's in your AI governance program for the last 30
//   days, and which frameworks does it touch."
//
// Layout:
//   1. Header
//   2. Configuration card — date range + 6 section toggles +
//      Export PDF button
//   3. Live preview of the report content. Toggling a section
//      hides/shows that section in both the preview and the export.
//   4. Frameworks reference card (US-foundation focused; EU note
//      called out separately because applicability varies)
//   5. Footer line
//
// Export flow:
//   Click "Export PDF" → render the same six sections into a
//   self-contained HTML string with inline print CSS → wrap as a
//   text/html Blob → open the Blob URL in a new window → that
//   window's onload triggers window.print(). The browser print
//   dialog handles Save as PDF / printer.
//
//   No PDF library, no server hop. Works on any browser that
//   supports window.print() — i.e. all of them.
//
// Data:
//   Every number is sourced from frontend/src/lib/curated-demo-
//   data.ts so the report reconciles with /overview, /feed,
//   /coaching, /policies. When M2 routers ship, swap the imports
//   for api.getAuditReport({ start, end, sections }) returning
//   the same shape — JSX (and the print HTML generator) unchanged.
//
// Replaces:
//   The older /audit-reports subtree (5 sub-pages) and the EU-AI-
//   Act-flavoured /compliance page. A US-foundation buyer wants
//   one export, not a navigable subtree.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { UseCaseCounts } from '@/lib/types';
import {
  ORG,
  COUNTS_30D,
  TOOL_USAGE,
  TOP_NUDGES,
  POLICIES,
  ACTIVITY_EVENTS,
  US_FRAMEWORKS,
  EU_AI_ACT_NOTE,
  type ActivityEvent,
} from '@/lib/curated-demo-data';

// ─── Section toggles ──────────────────────────────────────────────────

const SECTIONS = [
  { key: 'redacted', label: 'Redacted prompts summary' },
  { key: 'topUsers', label: 'Top users by event' },
  { key: 'tools', label: 'AI tool usage' },
  { key: 'policies', label: 'Policies in force' },
  { key: 'coaching', label: 'Coaching nudges delivered' },
  { key: 'bias', label: 'Bias-flagged grant prompts' },
  { key: 'useCases', label: 'Registered AI use cases' },
] as const;

type SectionKey = (typeof SECTIONS)[number]['key'];

const ALL_ON: Record<SectionKey, boolean> = {
  redacted: true,
  topUsers: true,
  tools: true,
  policies: true,
  coaching: true,
  bias: true,
  useCases: true,
};

type DateRange = '30d' | '90d' | 'ytd';

const RANGE_LABEL: Record<DateRange, string> = {
  '30d': 'Last 30 days',
  '90d': 'Last 90 days',
  ytd: 'Year to date',
};

// ─── Derived data ─────────────────────────────────────────────────────

interface UserRow {
  user: string;
  department: string;
  total: number;
  topEvent: string;
}

function topUsersByEvent(events: readonly ActivityEvent[], limit = 5): UserRow[] {
  const byUser = new Map<
    string,
    { dept: string; total: number; counts: Map<string, number> }
  >();
  for (const e of events) {
    const slot =
      byUser.get(e.user) ??
      { dept: e.department, total: 0, counts: new Map<string, number>() };
    slot.total += 1;
    slot.counts.set(e.eventType, (slot.counts.get(e.eventType) ?? 0) + 1);
    byUser.set(e.user, slot);
  }
  const rows: UserRow[] = [];
  byUser.forEach(({ dept, total, counts }, user) => {
    let topEvent = '—';
    let max = 0;
    counts.forEach((n, evtType) => {
      if (n > max) {
        max = n;
        topEvent = evtType;
      }
    });
    rows.push({ user, department: dept, total, topEvent });
  });
  rows.sort((a, b) => b.total - a.total);
  return rows.slice(0, limit);
}

function biasFlaggedEvents(
  events: readonly ActivityEvent[],
): { count: number; samples: ActivityEvent[] } {
  const filtered = events.filter((e) => e.eventType === 'Bias-flagged');
  return { count: filtered.length, samples: filtered.slice(0, 3) };
}

// ─── Print HTML generator ─────────────────────────────────────────────
//
// Produces a fully self-contained HTML document. The window that
// opens this Blob URL has no access to our Tailwind bundle, so the
// stylesheet is inlined and uses plain CSS. The <body> ends with a
// tiny onload script that calls window.print().

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildPrintHtml(
  sections: Record<SectionKey, boolean>,
  range: DateRange,
  generatedAtLabel: string,
  useCaseCounts: UseCaseCounts | null,
): string {
  const rangeLabel = RANGE_LABEL[range];
  const topUsers = topUsersByEvent(ACTIVITY_EVENTS);
  const bias = biasFlaggedEvents(ACTIVITY_EVENTS);
  const activePolicies = POLICIES.filter((p) => p.status === 'Active');

  const sectionHtml: string[] = [];

  if (sections.redacted) {
    sectionHtml.push(`
      <section>
        <h2>1. Redacted prompts summary</h2>
        <p class="lead"><strong>${COUNTS_30D.redactedPrompts.toLocaleString()}</strong>
          sensitive prompts were redacted or anonymised during the period.</p>
        <p>This is a <strong>${COUNTS_30D.riskyPromptsReductionPct}% reduction</strong>
          in risky prompts versus the prior 30 days. ${COUNTS_30D.highSeverityEvents}
          high-severity event${(COUNTS_30D.highSeverityEvents as number) === 1 ? '' : 's'} required
          manual review.</p>
      </section>`);
  }

  if (sections.topUsers) {
    sectionHtml.push(`
      <section>
        <h2>2. Top users by event volume</h2>
        <table>
          <thead><tr><th>User</th><th>Department</th><th>Events</th><th>Top event type</th></tr></thead>
          <tbody>
            ${topUsers
              .map(
                (r) => `<tr>
                  <td>${escapeHtml(r.user)}</td>
                  <td>${escapeHtml(r.department)}</td>
                  <td class="num">${r.total}</td>
                  <td>${escapeHtml(r.topEvent)}</td>
                </tr>`,
              )
              .join('')}
          </tbody>
        </table>
        <p class="small">Users are pseudonymous demo placeholders tagged <code>[DEMO]</code>.</p>
      </section>`);
  }

  if (sections.tools) {
    sectionHtml.push(`
      <section>
        <h2>3. AI tool usage</h2>
        <p>${COUNTS_30D.totalPrompts.toLocaleString()} prompts observed across the
          following tools:</p>
        <table>
          <thead><tr><th>Tool</th><th>Share</th><th>Sanctioned</th></tr></thead>
          <tbody>
            ${TOOL_USAGE.map(
              (t) => `<tr>
                <td>${escapeHtml(t.tool)}</td>
                <td class="num">${t.sharePct}%</td>
                <td>${t.isShadow ? 'Shadow' : 'Sanctioned'}</td>
              </tr>`,
            ).join('')}
          </tbody>
        </table>
      </section>`);
  }

  if (sections.policies) {
    sectionHtml.push(`
      <section>
        <h2>4. Policies in force</h2>
        <p>${activePolicies.length} policies were active during the period.</p>
        <table>
          <thead><tr><th>Policy</th><th>Rule</th><th>Sensitive type</th><th>Scope</th></tr></thead>
          <tbody>
            ${activePolicies
              .map(
                (p) => `<tr>
                  <td>${escapeHtml(p.name)}</td>
                  <td>${escapeHtml(p.rule)}</td>
                  <td>${escapeHtml(p.sensitiveType)}</td>
                  <td>${escapeHtml(p.scope.join(', '))}</td>
                </tr>`,
              )
              .join('')}
          </tbody>
        </table>
      </section>`);
  }

  if (sections.coaching) {
    sectionHtml.push(`
      <section>
        <h2>5. Coaching nudges delivered</h2>
        <p><strong>${COUNTS_30D.coachingNudges.toLocaleString()}</strong> in-line
          coaching nudges fired in the period (+${COUNTS_30D.successfulPromptsTrendPct}%
          successful prompts vs prior 30 days).</p>
        <table>
          <thead><tr><th>Nudge</th><th>Deliveries</th></tr></thead>
          <tbody>
            ${TOP_NUDGES.map(
              (n) => `<tr>
                <td>${escapeHtml(n.title)}</td>
                <td class="num">${n.deliveries.toLocaleString()}</td>
              </tr>`,
            ).join('')}
          </tbody>
        </table>
      </section>`);
  }

  if (sections.bias) {
    sectionHtml.push(`
      <section>
        <h2>6. Bias-flagged grant prompts</h2>
        <p><strong>${bias.count}</strong> grant-scoring prompts referenced
          protected characteristics and were flagged for review.</p>
        ${
          bias.samples.length
            ? `<table>
                 <thead><tr><th>When</th><th>Department</th><th>Detail</th></tr></thead>
                 <tbody>
                   ${bias.samples
                     .map(
                       (s) => `<tr>
                         <td>${new Date(s.timestamp).toLocaleDateString()}</td>
                         <td>${escapeHtml(s.department)}</td>
                         <td>${escapeHtml(s.detail)}</td>
                       </tr>`,
                     )
                     .join('')}
                 </tbody>
               </table>`
            : '<p class="small">No bias-flagged prompts in the period.</p>'
        }
      </section>`);
  }

  if (sections.useCases) {
    sectionHtml.push(`
      <section>
        <h2>7. Registered AI use cases</h2>
        ${
          useCaseCounts
            ? `<p><strong>${useCaseCounts.total.toLocaleString()}</strong>
                 use cases registered across the org (live API).</p>
               <table>
                 <thead><tr><th>Status</th><th class="num">Count</th></tr></thead>
                 <tbody>
                   <tr><td>Active</td><td class="num">${useCaseCounts.active}</td></tr>
                   <tr><td>Review</td><td class="num">${useCaseCounts.review}</td></tr>
                   <tr><td>Draft</td><td class="num">${useCaseCounts.draft}</td></tr>
                   <tr><td>Retired</td><td class="num">${useCaseCounts.retired}</td></tr>
                 </tbody>
               </table>`
            : '<p class="small">Live counts unavailable. Backend offline at export time.</p>'
        }
      </section>`);
  }

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Atlas Audit Report — ${escapeHtml(ORG.name)}</title>
<style>
  @page { size: A4; margin: 18mm 16mm; }
  body { font: 11pt/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         color: #111827; margin: 0; }
  header.brand { border-bottom: 2px solid #111827; padding-bottom: 12px; margin-bottom: 18px; }
  header.brand h1 { margin: 0; font-size: 18pt; }
  header.brand .meta { display: flex; justify-content: space-between;
                       font-size: 10pt; color: #4b5563; margin-top: 6px; }
  section { margin-bottom: 18px; page-break-inside: avoid; }
  h2 { font-size: 12pt; margin: 0 0 6px; color: #111827; border-left: 3px solid #04AA6D;
       padding-left: 8px; }
  p { margin: 6px 0; }
  p.lead { font-size: 12pt; }
  p.small { color: #6b7280; font-size: 9pt; }
  code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 9.5pt;
         background: #f3f4f6; padding: 0 3px; border-radius: 3px; }
  table { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 10pt; }
  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #e5e7eb; }
  th { background: #f9fafb; font-size: 9.5pt; text-transform: uppercase;
       letter-spacing: 0.04em; color: #6b7280; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  footer.ref { border-top: 1px solid #e5e7eb; padding-top: 10px; margin-top: 18px;
               font-size: 9pt; color: #4b5563; }
  footer.ref strong { color: #111827; }
</style>
</head>
<body>
  <header class="brand">
    <h1>${escapeHtml(ORG.name)} — AI governance audit</h1>
    <div class="meta">
      <span>Period: ${escapeHtml(rangeLabel)}</span>
      <span>Generated ${escapeHtml(generatedAtLabel)} by Atlas</span>
    </div>
  </header>
  ${sectionHtml.join('\n')}
  <footer class="ref">
    <strong>Frameworks referenced:</strong> NIST AI RMF 1.0 · IRS 990-PF ·
    MD MPIPA · MD MODPA · CA AI Transparency Act SB 942 · IL HB 3773 · TX TDPSA.
    EU AI Act applicability depends on jurisdiction — adjust per customer.
  </footer>
  <script>window.addEventListener('load', function () {
    setTimeout(function () { window.print(); }, 250);
  });</script>
</body>
</html>`;
}

// ─── Preview sub-components (in-page mirror of the print sections) ───

function PreviewSection({
  number,
  title,
  children,
}: {
  number: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-l-2 border-emerald-500 pl-4">
      <h3 className="text-sm font-semibold text-gray-900">
        {number}. {title}
      </h3>
      <div className="mt-2 text-sm text-gray-700">{children}</div>
    </div>
  );
}

function SimpleTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: (string | number)[][];
}) {
  return (
    <table className="mt-2 w-full text-xs">
      <thead>
        <tr className="text-left uppercase tracking-wide text-gray-500">
          {headers.map((h) => (
            <th key={h} className="border-b border-gray-200 px-2 py-1 font-medium">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {r.map((c, j) => (
              <td key={j} className="border-b border-gray-100 px-2 py-1 text-gray-700">
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function AuditReportPage() {
  const [range, setRange] = useState<DateRange>('30d');
  const [sections, setSections] = useState<Record<SectionKey, boolean>>(ALL_ON);
  const [useCaseCounts, setUseCaseCounts] = useState<UseCaseCounts | null>(
    null,
  );

  // Live fetch — soft-fails to null + the Registered Use Cases
  // section renders "—" in that case.
  useEffect(() => {
    let cancelled = false;
    api
      .getUseCaseCounts()
      .then((c) => {
        if (!cancelled) setUseCaseCounts(c);
      })
      .catch(() => {
        /* soft fail */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const topUsers = useMemo(() => topUsersByEvent(ACTIVITY_EVENTS), []);
  const bias = useMemo(() => biasFlaggedEvents(ACTIVITY_EVENTS), []);
  const activePolicies = useMemo(
    () => POLICIES.filter((p) => p.status === 'Active'),
    [],
  );

  const toggle = (key: SectionKey) =>
    setSections((s) => ({ ...s, [key]: !s[key] }));

  const exportPdf = () => {
    const generatedAt = new Date().toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
    const html = buildPrintHtml(sections, range, generatedAt, useCaseCounts);
    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const win = window.open(url, '_blank', 'noopener,noreferrer');
    if (!win) {
      alert(
        'Pop-up blocked. Allow pop-ups for this site to export the report.',
      );
      URL.revokeObjectURL(url);
      return;
    }
    // Revoke once the new tab has had time to fetch the Blob.
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const anySectionOn = Object.values(sections).some(Boolean);
  const enabledCount = Object.values(sections).filter(Boolean).length;

  return (
    <div>
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Audit &amp; Report</h1>
        <p className="mt-1 text-sm text-gray-600">
          {ORG.name} — one-page export for the AI governance review pack
        </p>
      </div>

      {/* Configuration */}
      <div className="mt-6 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[auto_1fr_auto]">
          {/* Date range */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Date range
            </p>
            <div className="mt-2 inline-flex rounded-lg border border-gray-200 bg-white p-0.5">
              {(['30d', '90d', 'ytd'] as const).map((r) => {
                const active = range === r;
                return (
                  <button
                    type="button"
                    key={r}
                    onClick={() => setRange(r)}
                    className={
                      active
                        ? 'rounded-md bg-primary-600 px-3 py-1 text-xs font-semibold text-white'
                        : 'rounded-md px-3 py-1 text-xs font-medium text-gray-600 hover:text-gray-900'
                    }
                  >
                    {RANGE_LABEL[r]}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-[11px] text-gray-500">
              Custom range — coming with M2 routers
            </p>
          </div>

          {/* Sections */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Sections
            </p>
            <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
              {SECTIONS.map((s) => (
                <label
                  key={s.key}
                  className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    checked={sections[s.key]}
                    onChange={() => toggle(s.key)}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                  />
                  <span className="text-sm text-gray-800">{s.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Export */}
          <div className="flex flex-col items-stretch justify-end gap-2 lg:items-end">
            <button
              type="button"
              disabled={!anySectionOn}
              onClick={exportPdf}
              className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              Export PDF →
            </button>
            <p className="text-[11px] text-gray-500 lg:text-right">
              {enabledCount} of {SECTIONS.length} sections selected
            </p>
          </div>
        </div>
      </div>

      {/* Preview */}
      <div className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <div className="flex items-center justify-between border-b border-gray-100 pb-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900">
              {ORG.name} — AI governance audit
            </h2>
            <p className="text-xs text-gray-500">
              Period: {RANGE_LABEL[range]} · preview reflects current
              selection
            </p>
          </div>
          <Link
            href="/dashboard/overview"
            className="text-xs font-medium text-primary-600 hover:text-primary-700"
          >
            Cross-check Overview →
          </Link>
        </div>

        <div className="mt-5 space-y-6">
          {sections.redacted && (
            <PreviewSection number={1} title="Redacted prompts summary">
              <p>
                <strong className="text-gray-900">
                  {COUNTS_30D.redactedPrompts.toLocaleString()}
                </strong>{' '}
                sensitive prompts were redacted or anonymised during the
                period.
              </p>
              <p className="mt-1">
                A{' '}
                <strong className="text-gray-900">
                  {COUNTS_30D.riskyPromptsReductionPct}% reduction
                </strong>{' '}
                in risky prompts vs the prior 30 days.{' '}
                {COUNTS_30D.highSeverityEvents} high-severity event
                {(COUNTS_30D.highSeverityEvents as number) === 1 ? '' : 's'} required
                review.
              </p>
            </PreviewSection>
          )}

          {sections.topUsers && (
            <PreviewSection number={2} title="Top users by event volume">
              <SimpleTable
                headers={['User', 'Department', 'Events', 'Top event']}
                rows={topUsers.map((r) => [
                  r.user,
                  r.department,
                  r.total,
                  r.topEvent,
                ])}
              />
            </PreviewSection>
          )}

          {sections.tools && (
            <PreviewSection number={3} title="AI tool usage">
              <p>
                {COUNTS_30D.totalPrompts.toLocaleString()} prompts
                observed across the following tools:
              </p>
              <SimpleTable
                headers={['Tool', 'Share', 'Sanctioned']}
                rows={TOOL_USAGE.map((t) => [
                  t.tool,
                  `${t.sharePct}%`,
                  t.isShadow ? 'Shadow' : 'Sanctioned',
                ])}
              />
            </PreviewSection>
          )}

          {sections.policies && (
            <PreviewSection number={4} title="Policies in force">
              <p>{activePolicies.length} policies active during the period.</p>
              <SimpleTable
                headers={['Policy', 'Rule', 'Sensitive type']}
                rows={activePolicies.map((p) => [
                  p.name,
                  p.rule,
                  p.sensitiveType,
                ])}
              />
            </PreviewSection>
          )}

          {sections.coaching && (
            <PreviewSection number={5} title="Coaching nudges delivered">
              <p>
                <strong className="text-gray-900">
                  {COUNTS_30D.coachingNudges.toLocaleString()}
                </strong>{' '}
                in-line coaching nudges fired (+
                {COUNTS_30D.successfulPromptsTrendPct}% successful prompts
                vs prior 30 days).
              </p>
              <SimpleTable
                headers={['Nudge', 'Deliveries']}
                rows={TOP_NUDGES.map((n) => [
                  n.title,
                  n.deliveries.toLocaleString(),
                ])}
              />
            </PreviewSection>
          )}

          {sections.bias && (
            <PreviewSection number={6} title="Bias-flagged grant prompts">
              <p>
                <strong className="text-gray-900">{bias.count}</strong>{' '}
                grant-scoring prompts referenced protected characteristics
                and were flagged for review.
              </p>
              {bias.samples.length > 0 && (
                <SimpleTable
                  headers={['When', 'Department', 'Detail']}
                  rows={bias.samples.map((s) => [
                    new Date(s.timestamp).toLocaleDateString(),
                    s.department,
                    s.detail,
                  ])}
                />
              )}
            </PreviewSection>
          )}

          {sections.useCases && (
            <PreviewSection number={7} title="Registered AI use cases">
              {useCaseCounts ? (
                <>
                  <p>
                    <strong className="text-gray-900">
                      {useCaseCounts.total.toLocaleString()}
                    </strong>{' '}
                    use cases registered across the org. Live counts
                    pulled from <code>/api/v1/use-cases/counts</code>.
                  </p>
                  <SimpleTable
                    headers={['Status', 'Count']}
                    rows={[
                      ['Active', useCaseCounts.active.toLocaleString()],
                      ['Review', useCaseCounts.review.toLocaleString()],
                      ['Draft', useCaseCounts.draft.toLocaleString()],
                      ['Retired', useCaseCounts.retired.toLocaleString()],
                    ]}
                  />
                </>
              ) : (
                <p className="text-gray-500">
                  Live counts unavailable. Connect the registry backend
                  (<code>/api/v1/use-cases</code>) to populate this
                  section.
                </p>
              )}
            </PreviewSection>
          )}

          {!anySectionOn && (
            <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-amber-100">
              No sections selected — pick at least one above to enable
              export.
            </p>
          )}
        </div>
      </div>

      {/* Frameworks reference */}
      <div className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-base font-semibold text-gray-900">
          Regulatory frameworks referenced
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          US foundation buyer focus. Adjust per customer jurisdiction.
        </p>
        <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {US_FRAMEWORKS.map((f) => (
            <div
              key={f.code}
              className="rounded-md bg-gray-50 px-3 py-2 ring-1 ring-gray-100"
            >
              <dt className="text-sm font-semibold text-gray-900">{f.code}</dt>
              <dd className="mt-1 text-xs text-gray-600">{f.note}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-900 ring-1 ring-blue-100">
          <strong>EU AI Act.</strong> {EU_AI_ACT_NOTE}
        </p>
      </div>

      {/* Footer */}
      <p className="mt-4 text-xs text-gray-500">
        Numbers reconcile with{' '}
        <Link
          href="/dashboard/overview"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Overview
        </Link>
        ,{' '}
        <Link
          href="/dashboard/feed"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Activity Log
        </Link>
        , and{' '}
        <Link
          href="/dashboard/coaching"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Coaching
        </Link>{' '}
        via the single source of truth in{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
          curated-demo-data.ts
        </code>
        .
      </p>
    </div>
  );
}
