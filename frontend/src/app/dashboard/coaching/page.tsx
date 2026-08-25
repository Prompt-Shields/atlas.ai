'use client';

// ─────────────────────────────────────────────────────────────────────
// Coaching & Adoption — atlas dashboard §3.3
//
// Purpose:
//   Prove "teach not block". Show that in-line nudges drive more
//   successful prompts and fewer risky ones. The page is the
//   counter-argument to every governance tool that ships a "you
//   must take this training" surface — coaching is in the flow.
//
// Layout (top → bottom):
//   1. Header
//   2. Four headline tiles
//   3. Top coaching nudges (purple proportional bars) +
//      Active AI users by department (shared bar list)
//   4. Adoption + proficiency heatmap placeholder
//      (rendered by PR #13's <AdoptionHeatmap /> once that lands)
//   5. Outcome callout — emerald band, the proof point
//
// Source of truth:
//   frontend/src/lib/curated-demo-data.ts exposes COUNTS_30D,
//   ORG, TOP_NUDGES, and ADOPTION_BY_DEPT — everything on this
//   page is one of those.
//
// Cuts from the older "Education & Training" page:
//   The AI Security 101 / Prompt Injection / Redaction Techniques
//   LMS-style module list is gone — pilot feedback: "too LMS-flavoured."
//   Coaching is in-line at the prompt, not a curriculum.
// ─────────────────────────────────────────────────────────────────────

import Link from 'next/link';
import { AdoptionBarList } from '@/components/spm/adoption-bar-list';
import {
  DEMO_DATA_BANNER,
  ORG,
  COUNTS_30D,
  TOP_NUDGES,
  ADOPTION_BY_DEPT,
  activeUserShare,
  activeUserPct,
  type CoachingNudge,
} from '@/lib/curated-demo-data';
import { isDemoFallbackEnabled } from '@/lib/demo-mode';

// ─── Sub-components ───────────────────────────────────────────────────

function HeadlineTile({
  label,
  value,
  trend,
  hint,
}: {
  label: string;
  value: string;
  trend?: { delta: string; tone: 'good' | 'bad'; direction: 'up' | 'down' };
  hint?: string;
}) {
  const trendClass =
    trend?.tone === 'good' ? 'text-emerald-600' : 'text-red-600';
  const arrow = trend?.direction === 'up' ? '▲' : '▼';
  return (
    <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </p>
      <p className="mt-2 text-3xl font-bold text-gray-900">{value}</p>
      {trend && (
        <p className={`mt-1 text-xs font-medium ${trendClass}`}>
          {arrow} {trend.delta}
        </p>
      )}
      {hint && <p className="mt-2 text-xs text-gray-500">{hint}</p>}
    </div>
  );
}

function TopNudges({ nudges }: { nudges: readonly CoachingNudge[] }) {
  const max = nudges.reduce((m, n) => Math.max(m, n.deliveries), 0) || 1;
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">
          Top coaching nudges
        </h3>
        <span className="text-xs text-gray-500">
          deliveries · last {ORG.reportingPeriodDays} days
        </span>
      </div>
      <ol className="mt-4 space-y-3">
        {nudges.map((n, idx) => {
          const width = Math.round((n.deliveries / max) * 100);
          return (
            <li key={n.title} className="grid grid-cols-[1.25rem_1fr] gap-2">
              <span className="select-none pt-0.5 text-xs font-semibold text-gray-400">
                {idx + 1}.
              </span>
              <div>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-sm text-gray-800">
                    {n.title}
                  </span>
                  <span className="shrink-0 text-xs font-semibold text-gray-900 tabular-nums">
                    {n.deliveries.toLocaleString()}
                  </span>
                </div>
                <div
                  className="mt-1 h-2 w-full rounded-full bg-gray-100"
                  aria-hidden="true"
                >
                  <div
                    className="h-2 rounded-full bg-purple-500"
                    style={{ width: `${width}%` }}
                  />
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="mt-4 text-xs text-gray-500">
        Each nudge fires in the input field, at the moment the user is
        typing — no separate LMS, no follow-up email.
      </p>
    </div>
  );
}

function ActiveUsersByDept() {
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">
          Active AI users by department
        </h3>
        <span className="text-xs text-gray-500">% of dept active</span>
      </div>
      <div className="mt-4">
        <AdoptionBarList rows={ADOPTION_BY_DEPT} />
      </div>
      <p className="mt-4 text-xs text-gray-500">
        Amber bars ≤ 60% — these are the departments to invite to the
        next coaching pilot. The full proficiency heatmap below shows
        the role-level breakdown.
      </p>
    </div>
  );
}

function HeatmapPlaceholder() {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 p-6 text-center">
      <h3 className="text-sm font-semibold text-gray-900">
        Adoption + proficiency heatmap
      </h3>
      <p className="mt-2 text-sm text-gray-600">
        Per-role active-user % × time-to-proficiency days. Lagging rows
        highlighted.
      </p>
      <p className="mt-3 text-xs text-gray-500">
        Renders here once PR #13 (<code>feat/m3-adoption-heatmap</code>) lands —
        this page imports{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
          @/components/spm/adoption-heatmap
        </code>{' '}
        and{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
          @/lib/adoption-proficiency
        </code>{' '}
        in the follow-up PR.
      </p>
    </div>
  );
}

function OutcomeCallout() {
  return (
    <div className="rounded-xl bg-emerald-50 px-5 py-4 ring-1 ring-emerald-100">
      <p className="text-sm text-emerald-900">
        <strong>Coaching works.</strong> Coached users showed a{' '}
        <strong>{COUNTS_30D.riskyPromptsReductionPct}% drop</strong> in
        risky prompts and a{' '}
        <strong>+{COUNTS_30D.successfulPromptsTrendPct}% rise</strong> in
        successful prompts vs the prior 30-day window. Coaching is
        in-line — there is no separate LMS to deploy.
      </p>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function CoachingPage() {
  if (!isDemoFallbackEnabled()) {
    return (
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h1 className="text-2xl font-bold text-gray-900">
          Coaching &amp; Adoption
        </h1>
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
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Coaching &amp; Adoption</h1>
        <p className="mt-1 text-sm text-gray-600">
          {ORG.name} — last {ORG.reportingPeriodDays} days · teach, don&apos;t block
        </p>
      </div>

      {/* Four headline tiles */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <HeadlineTile
          label="Active users"
          value={activeUserShare()}
          hint={`${activeUserPct()}% rolling ${ORG.reportingPeriodDays}d`}
        />
        <HeadlineTile
          label="Successful prompts"
          value={COUNTS_30D.totalPrompts.toLocaleString()}
          trend={{
            delta: `${COUNTS_30D.successfulPromptsTrendPct}% vs prior 30d`,
            tone: 'good',
            direction: 'up',
          }}
        />
        <HeadlineTile
          label="Coaching nudges delivered"
          value={COUNTS_30D.coachingNudges.toLocaleString()}
          hint="In-line at the prompt — no email, no LMS."
        />
        <HeadlineTile
          label="Risky prompts"
          value={`−${COUNTS_30D.riskyPromptsReductionPct}%`}
          trend={{
            delta: 'vs prior 30d',
            tone: 'good',
            direction: 'down',
          }}
        />
      </div>

      {/* Top nudges + dept adoption */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TopNudges nudges={TOP_NUDGES} />
        <ActiveUsersByDept />
      </div>

      {/* Heatmap placeholder until PR #13 lands */}
      <div className="mt-6">
        <HeatmapPlaceholder />
      </div>

      {/* Outcome callout — the proof point */}
      <div className="mt-6">
        <OutcomeCallout />
      </div>

      {/* Cross-link footer */}
      <p className="mt-4 text-xs text-gray-500">
        See every nudge delivery in the{' '}
        <Link
          href="/dashboard/feed?event=Coached"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Activity Log filtered to Coached events
        </Link>
        .
      </p>
    </div>
  );
}
