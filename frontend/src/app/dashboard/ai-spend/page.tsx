'use client';

// ─────────────────────────────────────────────────────────────────────
// AI Spend — tenant-wide AI cost ledger.
//
// Surfaces the cost router (GET /api/v1/cost/*): a summary strip
// (total / actual vs estimated / provisional / connectors), a daily
// time-series with provisional days flagged, and breakdown tables by
// provider and model with an Actual-vs-Estimated badge derived from
// each row's cost_source.
//
// No chart library is used anywhere in the repo (model-risk renders
// inline bars by hand, the rest are tables), so the time series is
// drawn as a simple, consistent inline-bar table — provisional days
// get a lighter bar + tag.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import {
  getCostSummary,
  getCostTimeseries,
  getCostBreakdown,
  getRoi,
  putRoiAssumptions,
  defaultWindow,
  type CostSummary,
  type CostTimeseriesPoint,
  type CostBreakdownRow,
  type HoursSavedBasis,
  type HoursSavedSource,
  type RoiResponse,
} from '@/lib/cost';

// ── Formatting helpers ──────────────────────────────────────────────

const usd = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatUsd(value: string | number): string {
  return usd.format(Number(value) || 0);
}

// ── Actual / Estimated / Mixed badge (driven by cost_source) ────────

type CostKind = 'actual' | 'estimated' | 'mixed';

function classifySource(source: string): CostKind {
  if (source === 'vendor_reported') return 'actual';
  if (source === 'mixed') return 'mixed';
  return 'estimated'; // derived_tokens, derived_seats
}

function SourceBadge({ source }: { source: string }) {
  const kind = classifySource(source);
  const style = {
    actual: 'bg-emerald-100 text-emerald-800',
    estimated: 'bg-amber-100 text-amber-800',
    mixed: 'bg-blue-100 text-blue-800',
  }[kind];
  const label = { actual: 'Actual', estimated: 'Estimated', mixed: 'Mixed' }[
    kind
  ];
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${style}`}>{label}</span>
  );
}

// ── Stat card ───────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </p>
      <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">
        {value}
      </p>
      {sub && <div className="mt-1 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

// ── AI adoption ROI ─────────────────────────────────────────────────
//
// Reads GET /cost/roi. The whole model is server-side (cost-ledger
// slice 3): the ledger supplies the measured half, the tenant's stored
// human-cost model supplies the estimated half.
//
// This section used to compute ROI in the browser from two numbers kept
// in localStorage, defaulting to 1240 hours/month at $95/h. Those were
// per-device, invisible to colleagues, and invented — and the backend's
// adoption scorecard meanwhile used $75/h, so an organisation read a
// different ROI depending on which page it opened. Everything below is
// display only; nothing here recomputes the figures.

const ROI_BASIS_LABEL: Record<HoursSavedBasis, string> = {
  measured: 'Measured',
  sampled: 'Illustrative',
  manual: 'Your estimate',
};

/**
 * Provenance badge for the hours-saved input.
 *
 * Not decoration: `sampled` means the hours figure is a representative
 * stand-in rather than this organisation's own usage, and presenting
 * that unmarked would show an illustration as a finding.
 */
function BasisBadge({ basis }: { basis: HoursSavedBasis }) {
  const style: Record<HoursSavedBasis, string> = {
    measured: 'bg-emerald-100 text-emerald-800',
    sampled: 'bg-amber-100 text-amber-800',
    manual: 'bg-blue-100 text-blue-800',
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${style[basis]}`}
    >
      {ROI_BASIS_LABEL[basis]}
    </span>
  );
}

/**
 * The assumptions behind the headline, readable by everyone and editable
 * by admins.
 *
 * Deliberately on this page rather than behind a settings route: the
 * numbers are the footnote to the figure directly above them, and an
 * assumption nobody can see while reading the result may as well be
 * hard-coded — which is what it was.
 *
 * Saving writes through the API, which audits the change. There is no
 * local copy of these values; after a save the page refetches so the
 * headline and the assumptions can never disagree on screen.
 */
function AssumptionsPanel({
  roi,
  canEdit,
  onSaved,
}: {
  roi: RoiResponse;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [rate, setRate] = useState(Number(roi.blended_hourly_rate_usd) || 0);
  const [source, setSource] = useState<HoursSavedSource>(
    roi.basis === 'manual' ? 'manual' : 'adoption_pipeline',
  );
  const [manualHours, setManualHours] = useState(
    roi.basis === 'manual' ? Number(roi.hours_saved_per_month) || 0 : 0,
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      await putRoiAssumptions({
        blended_hourly_rate_usd: rate,
        hours_saved_source: source,
        manual_hours_saved_per_month:
          source === 'manual' ? manualHours : null,
      });
      setEditing(false);
      onSaved();
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <div className="rounded-lg bg-gray-50 p-3 ring-1 ring-gray-200">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
          Assumptions
        </p>
        <dl className="mt-2 space-y-1 text-xs text-gray-600">
          <div className="flex justify-between gap-6">
            <dt>Loaded hourly rate</dt>
            <dd className="font-medium text-gray-900 tabular-nums">
              {formatUsd(roi.blended_hourly_rate_usd)}/h
            </dd>
          </div>
          <div className="flex justify-between gap-6">
            <dt>Hours saved / month</dt>
            <dd className="font-medium text-gray-900 tabular-nums">
              {Number(roi.hours_saved_per_month).toLocaleString('en-US')} h
            </dd>
          </div>
        </dl>
        {canEdit ? (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="mt-2 text-[11px] font-medium text-primary-700 hover:text-primary-800 hover:underline"
          >
            Edit assumptions
          </button>
        ) : (
          <p className="mt-2 text-[11px] text-gray-400">
            Set for the whole organisation by an admin.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg bg-gray-50 p-3 ring-1 ring-gray-200">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
        Assumptions
      </p>
      <div className="mt-2 flex flex-col gap-3">
        <label className="flex flex-col text-xs text-gray-600">
          <span className="mb-1">Loaded hourly rate (USD)</span>
          <input
            type="number"
            min={1}
            step={5}
            value={rate}
            onChange={(e) => setRate(Math.max(0, Number(e.target.value) || 0))}
            className="w-48 rounded border border-gray-300 px-2 py-1 text-sm text-gray-900 tabular-nums focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          />
          <span className="mt-1 text-[11px] text-gray-400">
            Salary plus employer overhead, not take-home.
          </span>
        </label>

        <label className="flex flex-col text-xs text-gray-600">
          <span className="mb-1">Hours saved from</span>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value as HoursSavedSource)}
            className="w-48 rounded border border-gray-300 px-2 py-1 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="adoption_pipeline">Adoption pipeline</option>
            <option value="manual">Our own estimate</option>
          </select>
        </label>

        {source === 'manual' && (
          <label className="flex flex-col text-xs text-gray-600">
            <span className="mb-1">Hours saved / month</span>
            <input
              type="number"
              min={0}
              step={10}
              value={manualHours}
              onChange={(e) =>
                setManualHours(Math.max(0, Number(e.target.value) || 0))
              }
              className="w-48 rounded border border-gray-300 px-2 py-1 text-sm text-gray-900 tabular-nums focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>
        )}

        {saveError && (
          <p className="text-[11px] text-red-600">{saveError}</p>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            disabled={saving || rate <= 0}
            onClick={() => void save()}
            className="rounded bg-primary-600 px-3 py-1 text-xs font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => {
              setEditing(false);
              setSaveError(null);
            }}
            className="rounded px-3 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100"
          >
            Cancel
          </button>
        </div>
        <p className="text-[11px] text-gray-400">
          Applies to everyone in your organisation. The change is recorded in
          the audit log.
        </p>
      </div>
    </div>
  );
}

function AdoptionRoiSection({
  roi,
  canEdit,
  onSaved,
}: {
  roi: RoiResponse | null;
  canEdit: boolean;
  onSaved: () => void;
}) {
  if (roi === null) {
    return (
      <section className="mt-8 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">AI adoption ROI</h2>
        <p className="mt-2 text-sm italic text-gray-500">
          ROI is unavailable for this window.
        </p>
      </section>
    );
  }

  const aiSpend = Number(roi.ai_spend_usd) || 0;
  const humanValue = Number(roi.human_value_usd) || 0;
  const netValue = Number(roi.net_value_usd) || 0;
  const multiplier =
    roi.roi_multiplier === null ? null : Number(roi.roi_multiplier);

  // Share of the human-equivalent value that AI spend consumes.
  const spendBarPct =
    humanValue > 0
      ? Math.min(Math.max((aiSpend / humanValue) * 100, 0), 100)
      : 0;

  return (
    <section className="mt-8 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-900">
              AI adoption ROI
            </h2>
            <BasisBadge basis={roi.basis} />
          </div>
          <p className="mt-1 text-xs text-gray-500">
            Human-equivalent value of AI-assisted work versus what you spend
            on AI tooling, over the last {roi.window_days} days.
          </p>
        </div>

        <AssumptionsPanel roi={roi} canEdit={canEdit} onSaved={onSaved} />
      </div>

      {/* The caveat that makes the number honest. Rendered whenever the
          hours-saved input is anything but this tenant's measured usage,
          which today is always. */}
      {roi.is_illustrative && (
        <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 ring-1 ring-amber-200">
          {roi.basis_detail}
        </p>
      )}

      {/* KPI cards */}
      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="AI spend (measured)"
          value={formatUsd(aiSpend)}
          sub="ledger total this window"
        />
        <StatCard
          label="Hours saved"
          value={`${Number(roi.hours_saved_in_window).toLocaleString('en-US')} h`}
          sub="this window (estimated)"
        />
        <StatCard
          label="Human-equivalent value"
          value={formatUsd(humanValue)}
          sub="hours × loaded rate"
        />
        <StatCard
          label="Net value"
          value={formatUsd(netValue)}
          sub={netValue < 0 ? 'AI costs more than it saves' : 'human value − AI spend'}
        />
        <div className="rounded-xl bg-primary-600 p-5 shadow-sm ring-1 ring-primary-700">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-primary-100">
            Return on AI
          </p>
          <p className="mt-1 text-2xl font-bold text-white tabular-nums">
            {multiplier === null ? '—' : `${multiplier.toFixed(1)}×`}
          </p>
          <p className="mt-1 text-xs text-primary-100">
            {/* Null is "undefined ratio", not "zero". Showing a number here
                for a tenant with no spend would be an unsupported claim. */}
            {multiplier === null
              ? 'No AI spend in this window to compare against'
              : 'per $1 spent on AI tooling'}
          </p>
        </div>
      </div>

      {/* Comparison bars */}
      <div className="mt-6">
        {humanValue > 0 ? (
          <div className="space-y-3">
            <div>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="font-medium text-gray-700">
                  Human-equivalent value
                </span>
                <span className="font-medium text-gray-900 tabular-nums">
                  {formatUsd(humanValue)}
                </span>
              </div>
              <div className="h-3 w-full rounded-full bg-gray-100">
                <div
                  className="h-3 rounded-full bg-emerald-500"
                  style={{ width: '100%' }}
                />
              </div>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="font-medium text-gray-700">AI spend</span>
                <span className="font-medium text-gray-900 tabular-nums">
                  {formatUsd(aiSpend)}
                </span>
              </div>
              <div className="h-3 w-full rounded-full bg-gray-100">
                <div
                  className="h-3 rounded-full bg-indigo-500"
                  style={{ width: `${spendBarPct}%` }}
                />
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm italic text-gray-500">
            No human-equivalent value to compare — set an hours-saved
            assumption in Settings.
          </p>
        )}
      </div>
    </section>
  );
}

// ── Time-series (inline bars) ───────────────────────────────────────

function TimeseriesSection({ points }: { points: CostTimeseriesPoint[] }) {
  if (points.length === 0) {
    return (
      <p className="text-sm text-gray-500 italic">
        No daily spend recorded in this window.
      </p>
    );
  }
  const max = Math.max(...points.map((p) => Number(p.cost_usd) || 0), 0.01);
  return (
    <div className="space-y-1">
      {points.map((p) => {
        const value = Number(p.cost_usd) || 0;
        const pct = Math.max((value / max) * 100, value > 0 ? 2 : 0);
        return (
          <div key={p.date} className="flex items-center gap-3 text-xs">
            <span className="w-24 shrink-0 text-gray-500 tabular-nums">
              {p.date}
            </span>
            <div className="h-3 flex-1 rounded-full bg-gray-100">
              <div
                className={`h-3 rounded-full ${
                  p.is_provisional ? 'bg-indigo-300' : 'bg-indigo-500'
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="w-20 shrink-0 text-right font-medium text-gray-900 tabular-nums">
              {formatUsd(value)}
            </span>
            <span className="w-20 shrink-0">
              {p.is_provisional && (
                <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600">
                  provisional
                </span>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Breakdown table ─────────────────────────────────────────────────

function BreakdownTable({
  title,
  rows,
  keyHeader,
}: {
  title: string;
  rows: CostBreakdownRow[];
  keyHeader: string;
}) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-gray-900">{title}</h3>
      <div className="overflow-hidden rounded border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left">
            <tr>
              <th className="px-3 py-2">{keyHeader}</th>
              <th className="px-3 py-2 text-right">Cost</th>
              <th className="px-3 py-2">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={3}
                  className="py-6 text-center italic text-gray-500"
                >
                  No spend in this window.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.key} className="border-t">
                <td className="px-3 py-2 font-medium text-gray-900">
                  {r.key || <span className="text-gray-400">—</span>}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-900">
                  {formatUsd(r.cost_usd)}
                </td>
                <td className="px-3 py-2">
                  <SourceBadge source={r.cost_source} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────

export default function AiSpendPage() {
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [series, setSeries] = useState<CostTimeseriesPoint[]>([]);
  const [byProvider, setByProvider] = useState<CostBreakdownRow[]>([]);
  const [byModel, setByModel] = useState<CostBreakdownRow[]>([]);
  const [byMember, setByMember] = useState<CostBreakdownRow[]>([]);
  const [roi, setRoi] = useState<RoiResponse | null>(null);
  // Editing the assumptions changes the number the whole organisation
  // reads, so it is admin-gated in the UI as well as on the API.
  const [canEdit, setCanEdit] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const window = defaultWindow();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, ts, prov, model, member, roiResult] = await Promise.all([
        getCostSummary(),
        getCostTimeseries(),
        getCostBreakdown('provider'),
        getCostBreakdown('model'),
        getCostBreakdown('member'),
        getRoi(),
      ]);
      setSummary(s);
      setSeries(ts);
      setByProvider(prov);
      setByModel(model);
      setByMember(member);
      setRoi(roiResult);
      try {
        const me = await api.getMe();
        setCanEdit(
          (me.roles || []).some((r: string) =>
            ['SUPER_ADMIN', 'TENANT_ADMIN', 'ORG_ADMIN'].includes(r),
          ),
        );
      } catch {
        // Role lookup is not worth failing the page over — the API
        // rejects a non-admin write regardless, so the worst case is a
        // button that is not offered.
        setCanEdit(false);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="py-12 text-center text-gray-500 animate-pulse">
        Loading AI spend…
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900">AI spend</h1>
        <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p className="font-medium">Couldn’t load cost data.</p>
          <p className="mt-1">{error}</p>
          <button
            onClick={() => void load()}
            className="mt-3 rounded border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const hasData =
    !!summary &&
    (summary.active_connectors > 0 ||
      Number(summary.total_cost_usd) > 0 ||
      series.length > 0);

  if (!hasData) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900">AI spend</h1>
        <p className="mt-1 text-sm text-gray-600">
          Track actual and estimated AI vendor spend across your tenant.
        </p>
        <div className="mt-8 rounded-xl border border-dashed border-gray-300 bg-white p-10 text-center">
          <p className="text-3xl">💵</p>
          <h2 className="mt-3 text-lg font-semibold text-gray-900">
            No AI spend yet
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-gray-600">
            Connect an AI vendor (OpenAI, Anthropic, and more) to start
            tracking actual and estimated spend here.
          </p>
          <Link
            href="/dashboard/integrations"
            className="mt-4 inline-block rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            Connect a vendor →
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">AI spend</h1>
          <p className="mt-1 text-sm text-gray-600">
            {window.since} → {window.until} · actual (vendor-reported) vs
            estimated (derived from usage)
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <a
            href="/ai-spend-roi-demo.html"
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Presentation demo ↗
          </a>
          <button
            onClick={() => void load()}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Summary strip */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total spend"
          value={formatUsd(summary.total_cost_usd)}
          sub="across all connected vendors"
        />
        <StatCard
          label="Actual vs estimated"
          value={formatUsd(summary.vendor_reported_usd)}
          sub={
            <span>
              <span className="font-medium text-emerald-700">Actual</span> ·{' '}
              <span className="font-medium text-amber-700">
                {formatUsd(summary.derived_usd)}
              </span>{' '}
              estimated
            </span>
          }
        />
        <StatCard
          label="Provisional (today)"
          value={formatUsd(summary.provisional_usd)}
          sub="not yet finalised by vendor"
        />
        <StatCard
          label="Active connectors"
          value={String(summary.active_connectors)}
          sub={
            <Link
              href="/dashboard/integrations"
              className="text-primary-600 hover:text-primary-700"
            >
              Manage →
            </Link>
          }
        />
      </div>

      {/* AI adoption ROI */}
      <AdoptionRoiSection roi={roi} canEdit={canEdit} onSaved={() => void load()} />

      {/* Time series */}
      <section className="mt-8 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
        <h2 className="mb-4 text-sm font-semibold text-gray-900">
          Daily spend
        </h2>
        <TimeseriesSection points={series} />
      </section>

      {/* Breakdowns */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <BreakdownTable
          title="By vendor"
          rows={byProvider}
          keyHeader="Provider"
        />
        <BreakdownTable title="By model" rows={byModel} keyHeader="Model" />
      </div>

      {byMember.length > 0 && (
        <div className="mt-6">
          <BreakdownTable
            title="By member"
            rows={byMember}
            keyHeader="Member"
          />
        </div>
      )}
    </div>
  );
}
