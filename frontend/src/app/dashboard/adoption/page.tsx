'use client';

// ─────────────────────────────────────────────────────────────────────
// Adoption (PRO-50, usage-quality upgrade #249) — vendor-centric "is
// anyone actually using this" page, headlined by a usage-quality
// scorecard (ROI/upskilling + outcome/leverage/trajectory/diffusion)
// fetched from GET /adoption/usage-quality.
//
// The AISPM source page is otherwise API-driven (/api/adoption/summary,
// /api/tour-engagement), which atlas.ai does not expose beyond the
// scorecard. Top tools by usage and the per-tool risk-action mix remain a
// SEEDED demo from @/lib/aispm/adoption-seed (badged SAMPLE DATA).
//
// The one other real signal woven in is atlas's existing ADOPTION_BY_DEPT
// from @/lib/curated-demo-data, rendered via the shared AdoptionBarList —
// the same data the Overview and Coaching pages already use.
//
// Visual language mirrors the rest of the dashboard: rounded-xl cards,
// ring-1 ring-gray-200, bg-white. No new design tokens, no chart lib.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import { AdoptionBarList } from '@/components/spm/adoption-bar-list';
import { UsageQualitySection } from '@/components/spm/usage-quality-section';
import { ADOPTION_BY_DEPT } from '@/lib/curated-demo-data';
import { adoptionApi, type UsageQualityScorecard } from '@/lib/aispm/adoption';
import {
  ADOPTION_OVERVIEW,
  TOP_TOOLS,
  RISK_ACTION_BREAKDOWN,
} from '@/lib/aispm/adoption-seed';

const num = new Intl.NumberFormat('en-US');

function SampleDataBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
      SAMPLE DATA
    </span>
  );
}

export default function AdoptionPage() {
  const [scorecard, setScorecard] = useState<UsageQualityScorecard | null>(null);
  const [scorecardError, setScorecardError] = useState<string | null>(null);
  const [scorecardLoading, setScorecardLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setScorecardLoading(true);
    adoptionApi
      .usageQuality()
      .then((res) => {
        if (!cancelled) setScorecard(res);
      })
      .catch((e) => {
        if (!cancelled) {
          setScorecardError(e instanceof Error ? e.message : 'Failed to load usage quality');
        }
      })
      .finally(() => {
        if (!cancelled) setScorecardLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tools = [...TOP_TOOLS].sort((a, b) => b.activeUsers - a.activeUsers);

  // "Share of active users" — normalise top tools against the leader so
  // the AdoptionBarList (0–100 domain) reads as a relative-share bar.
  const maxActive = Math.max(...tools.map((t) => t.activeUsers), 1);
  const toolShareRows = tools.map((t) => ({
    name: `${t.tool} · ${t.vendor}`,
    percent: Math.round((t.activeUsers / maxActive) * 100),
  }));

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">Adoption</h1>
          <SampleDataBadge />
        </div>
        <p className="mt-1 text-sm text-gray-600">
          Real-world usage of AI tools across the organisation — which
          vendors employees reach for, and how safely. Demo figures shown
          until adoption telemetry is wired up.
        </p>
      </div>

      {/* 1 — Usage-quality scorecard: ROI/upskilling + 4 quality dimensions */}
      {scorecardError ? (
        <p className="text-sm text-rose-600">{scorecardError}</p>
      ) : scorecardLoading || !scorecard ? (
        <div className="rounded-xl bg-white p-6 text-sm text-gray-500 shadow-sm ring-1 ring-gray-200">
          Loading usage quality…
        </div>
      ) : (
        <UsageQualitySection scorecard={scorecard} />
      )}

      {/* 2 — Top AI tools by usage (vendor-centric) */}
      <section className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-gray-900">
            Top AI tools by usage
          </h2>
          <SampleDataBadge />
        </div>
        <p className="mt-1 text-xs text-gray-500">
          Active users and prompt volume by tool and vendor over the last{' '}
          {ADOPTION_OVERVIEW.windowDays} days.
        </p>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-500">
                <th className="py-2 pr-4 font-medium">Tool</th>
                <th className="py-2 pr-4 font-medium">Vendor</th>
                <th className="py-2 pr-4 text-right font-medium">
                  Active users
                </th>
                <th className="py-2 text-right font-medium">Prompts</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((t) => (
                <tr
                  key={t.tool}
                  className="border-b border-gray-100 last:border-0"
                >
                  <td className="py-2 pr-4 font-medium text-gray-900">
                    {t.tool}
                  </td>
                  <td className="py-2 pr-4 text-gray-600">{t.vendor}</td>
                  <td className="py-2 pr-4 text-right tabular-nums text-gray-900">
                    {num.format(t.activeUsers)}
                  </td>
                  <td className="py-2 text-right tabular-nums text-gray-900">
                    {num.format(t.prompts)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-6">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Share of active users
          </h3>
          <div className="mt-3">
            <AdoptionBarList rows={toolShareRows} sort={false} />
          </div>
        </div>
      </section>

      {/* 3 — Adoption by department (real atlas signal) */}
      <section className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">
          Adoption by department
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          Active-user share by department — the same signal shown on the
          Overview and Coaching pages.
        </p>
        <div className="mt-4">
          <AdoptionBarList rows={ADOPTION_BY_DEPT} />
        </div>
      </section>

      {/* 4 — Risk actions by tool */}
      <section className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-gray-900">
            Risk actions by tool
          </h2>
          <SampleDataBadge />
        </div>
        <p className="mt-1 text-xs text-gray-500">
          How prompts to each tool were handled by policy: allowed, redacted,
          or blocked.
        </p>
        <ul className="mt-4 space-y-4">
          {RISK_ACTION_BREAKDOWN.map((r) => {
            const total = r.allowed + r.redacted + r.blocked || 1;
            const pct = (n: number) => (n / total) * 100;
            return (
              <li key={r.tool}>
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium text-gray-900">{r.tool}</span>
                  <span className="text-gray-500">
                    {num.format(total)} prompts
                  </span>
                </div>
                <div className="mt-1 flex h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-2.5 bg-emerald-500"
                    style={{ width: `${pct(r.allowed)}%` }}
                    title={`Allowed: ${num.format(r.allowed)}`}
                  />
                  <div
                    className="h-2.5 bg-amber-500"
                    style={{ width: `${pct(r.redacted)}%` }}
                    title={`Redacted: ${num.format(r.redacted)}`}
                  />
                  <div
                    className="h-2.5 bg-rose-500"
                    style={{ width: `${pct(r.blocked)}%` }}
                    title={`Blocked: ${num.format(r.blocked)}`}
                  />
                </div>
              </li>
            );
          })}
        </ul>
        <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-gray-500">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> Allowed
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-amber-500" /> Redacted
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-rose-500" /> Blocked
          </span>
        </div>
      </section>
    </div>
  );
}
