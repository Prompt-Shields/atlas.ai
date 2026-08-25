'use client';

// ─────────────────────────────────────────────────────────────────────
// AI Inventory — /dashboard/ai-inventory
//
// Customer-driven (Øystein Endal #2): "Kartlegge eksisterende bruk"
// — map existing AI usage across the org.
//
// Atlas already collects this data across three sources:
//   • REGISTERED — atlas's /dashboard/registry (form + survey-bot)
//   • SHADOW     — Defender CASB + Promptly extension detections
//   • SANCTIONED — IT-approved tools surfaced by the Integrations
//                  layer (e.g. Microsoft Copilot connected via Entra ID)
//
// This page joins all three into one canonical "what AI is in your
// org right now" view. Filterable by source / department / risk;
// exportable as CSV for board reporting.
//
// Data sources:
//   - REGISTERED_USE_CASES (live api.listUseCases() later — soft fail)
//   - SHADOW_APP_DETECTIONS
//   - INTEGRATION_CATALOGUE (sanctioned tools)
//   - MODEL_RISK_PROFILES (for the risk badges)
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  DEMO_DATA_BANNER,
  INTEGRATION_CATALOGUE,
  MODEL_RISK_PROFILES,
  ORG,
  REGISTERED_USE_CASES,
  SHADOW_APP_DETECTIONS,
  type IntegrationCard,
  type ModelRiskProfile,
  type RegisteredUseCase,
  type ShadowAppDetection,
} from '@/lib/curated-demo-data';
import { CsvImportDialog } from '@/components/ai-inventory/CsvImportDialog';
import { InventoryAggregateCard } from '@/components/ai-inventory/InventoryAggregateCard';
import { listAIAssets, type AIAsset } from '@/lib/ai-spm';
import { isDemoFallbackEnabled } from '@/lib/demo-mode';

// ─── Unified inventory row ───────────────────────────────────────────

type InventorySource = 'REGISTERED' | 'SHADOW' | 'SANCTIONED';

interface InventoryRow {
  key: string;
  title: string;
  tool: string;
  source: InventorySource;
  department: string;
  status: string;
  owner: string;
  riskTier: 'Low' | 'Medium' | 'High' | 'Unknown';
  detectedUsers: number;
  lastObservedAt: string;
  meta: string;
}

// Map model name → vendor risk profile so we can tag rows with the
// underlying model's risk score even when the row didn't carry it.
const MODEL_BY_NAME = new Map<string, ModelRiskProfile>(
  MODEL_RISK_PROFILES.map((m) => [m.modelName.toLowerCase(), m]),
);

function riskFromTool(tool: string): 'Low' | 'Medium' | 'High' | 'Unknown' {
  const lc = tool.toLowerCase();
  // Heuristic: match the tool name against known model names. Real
  // backend version will join to ModelRiskProfile via foreign key.
  let resolved: 'Low' | 'Medium' | 'High' | null = null;
  MODEL_BY_NAME.forEach((profile, name) => {
    if (resolved !== null) return;
    if (lc.includes(name.split(' ')[0])) {
      const composite =
        profile.hallucinationScore +
        profile.biasScore +
        profile.toxicityScore +
        profile.privacyScore +
        profile.securityScore +
        profile.complianceScore;
      resolved = composite < 100 ? 'Low' : composite < 150 ? 'Medium' : 'High';
    }
  });
  return resolved ?? 'Unknown';
}

function registeredUseCaseToRow(uc: RegisteredUseCase): InventoryRow {
  return {
    key: `uc-${uc.id}`,
    title: uc.title,
    tool: uc.tool,
    source: 'REGISTERED',
    department: uc.department,
    status: uc.status,
    owner: uc.owner,
    riskTier: uc.riskTier === 'Low'
      ? 'Low'
      : uc.riskTier === 'Medium'
        ? 'Medium'
        : 'High',
    detectedUsers: 1,
    lastObservedAt: uc.registeredAt,
    meta: `${uc.dataClasses.length} data classes`,
  };
}

function assetToRow(asset: AIAsset): InventoryRow {
  const score = asset.risk_score;
  return {
    key: `asset-${asset.id}`,
    title: asset.name,
    tool: asset.model_type ?? 'Not specified',
    source: 'REGISTERED',
    department: 'Not specified',
    status: asset.deployment_status,
    owner: asset.owner_id ? `Owner ${asset.owner_id.slice(0, 8)}` : 'Unassigned',
    riskTier:
      score === null
        ? 'Unknown'
        : score >= 70
          ? 'High'
          : score >= 40
            ? 'Medium'
            : 'Low',
    detectedUsers: 0,
    lastObservedAt: asset.updated_at,
    meta: asset.description ?? asset.hosting_location ?? 'No description',
  };
}

function shadowToRow(s: ShadowAppDetection): InventoryRow {
  const score = s.riskScore;
  return {
    key: `shadow-${s.id}`,
    title: s.appName,
    tool: s.appName,
    source: 'SHADOW',
    department: '—',
    status: s.status,
    owner: '—',
    riskTier: score >= 7 ? 'High' : score >= 5 ? 'Medium' : 'Low',
    detectedUsers: s.detectedUsers,
    lastObservedAt: s.lastSeen,
    meta: s.appCategory,
  };
}

function integrationToRow(card: IntegrationCard): InventoryRow | null {
  // Officially deployed = connected integrations of category 'data' or
  // ones whose display name maps to a known AI tool. Conservative:
  // only emit when status=CONNECTED.
  if (card.status !== 'CONNECTED') return null;
  // Filter to AI-content-bearing integrations; exclude infra ones
  // like Slack or device-management MDMs which aren't themselves
  // AI tools.
  const aiRelated = card.meta.category === 'data' || card.meta.provider.includes('COPILOT');
  if (!aiRelated && !card.meta.shortName.toLowerCase().includes('copilot')) {
    // Microsoft Copilot lives under Entra ID's organizational app
    // surface — promote here even though category=identity.
    if (card.meta.provider !== 'MICROSOFT_ENTRA_ID') return null;
  }
  return {
    key: `int-${card.meta.provider}`,
    title: `${card.meta.displayName} (org-sanctioned)`,
    tool: card.meta.shortName,
    source: 'SANCTIONED',
    department: 'Tenant-wide',
    status: 'Active',
    owner: 'IT',
    riskTier: riskFromTool(card.meta.shortName),
    detectedUsers: 0,
    lastObservedAt: card.connectedAt || '',
    meta: 'IT-approved integration',
  };
}

// ─── CSV export ───────────────────────────────────────────────────────

function exportCsv(rows: InventoryRow[]): void {
  const header = [
    'source',
    'title',
    'tool',
    'department',
    'status',
    'owner',
    'risk_tier',
    'detected_users',
    'last_observed_at',
    'meta',
  ];
  const body = rows.map((r) => [
    r.source,
    r.title,
    r.tool,
    r.department,
    r.status,
    r.owner,
    r.riskTier,
    String(r.detectedUsers),
    r.lastObservedAt,
    r.meta,
  ]);
  const csv = [header, ...body]
    .map((row) =>
      row
        .map((cell) => {
          const s = String(cell ?? '');
          if (s.includes(',') || s.includes('"') || s.includes('\n')) {
            return '"' + s.replace(/"/g, '""') + '"';
          }
          return s;
        })
        .join(','),
    )
    .join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `atlas-ai-inventory-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ─── Chips ────────────────────────────────────────────────────────────

const SOURCE_CHIP: Record<InventorySource, string> = {
  REGISTERED: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  SHADOW: 'bg-amber-50 text-amber-700 ring-amber-200',
  SANCTIONED: 'bg-blue-50 text-blue-700 ring-blue-200',
};

const SOURCE_LABEL: Record<InventorySource, string> = {
  REGISTERED: 'Registered',
  SHADOW: 'Shadow',
  SANCTIONED: 'Sanctioned',
};

function SourceChip({ source }: { source: InventorySource }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${SOURCE_CHIP[source]}`}
    >
      {SOURCE_LABEL[source]}
    </span>
  );
}

function RiskChip({ tier }: { tier: InventoryRow['riskTier'] }) {
  const cls =
    tier === 'High'
      ? 'bg-red-50 text-red-700 ring-red-200'
      : tier === 'Medium'
        ? 'bg-amber-50 text-amber-700 ring-amber-200'
        : tier === 'Low'
          ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
          : 'bg-gray-100 text-gray-700 ring-gray-200';
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${cls}`}
    >
      {tier}
    </span>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function AiInventoryPage() {
  const [sourceFilter, setSourceFilter] = useState<InventorySource | 'all'>(
    'all',
  );
  const [riskFilter, setRiskFilter] = useState<
    InventoryRow['riskTier'] | 'all'
  >('all');
  const [search, setSearch] = useState('');
  const [assets, setAssets] = useState<AIAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void listAIAssets({ pageSize: 100 })
      .then((response) => {
        if (active) setAssets(response.assets);
      })
      .catch((cause: unknown) => {
        if (active) {
          setError(
            cause instanceof Error ? cause.message : 'Failed to load AI assets.',
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const isDemo = !loading && assets.length === 0 && isDemoFallbackEnabled();

  // The table is live from the canonical AI asset registry. Legacy curated
  // rows remain available only when the explicit demo fallback is enabled.
  const allRows = useMemo<InventoryRow[]>(() => {
    if (assets.length > 0) return assets.map(assetToRow);
    if (!isDemo) return [];

    const rows: InventoryRow[] = [];
    REGISTERED_USE_CASES.forEach((uc) => rows.push(registeredUseCaseToRow(uc)));
    SHADOW_APP_DETECTIONS.forEach((s) => rows.push(shadowToRow(s)));
    INTEGRATION_CATALOGUE.forEach((c) => {
      const row = integrationToRow(c);
      if (row) rows.push(row);
    });
    return rows;
  }, [assets, isDemo]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allRows.filter((r) => {
      if (sourceFilter !== 'all' && r.source !== sourceFilter) return false;
      if (riskFilter !== 'all' && r.riskTier !== riskFilter) return false;
      if (q) {
        const hay =
          `${r.title} ${r.tool} ${r.department} ${r.owner} ${r.meta}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [allRows, sourceFilter, riskFilter, search]);

  const counts = useMemo(() => {
    const c = {
      total: allRows.length,
      registered: 0,
      shadow: 0,
      sanctioned: 0,
      high_risk: 0,
    };
    for (const r of allRows) {
      if (r.source === 'REGISTERED') c.registered += 1;
      else if (r.source === 'SHADOW') c.shadow += 1;
      else if (r.source === 'SANCTIONED') c.sanctioned += 1;
      if (r.riskTier === 'High') c.high_risk += 1;
    }
    return c;
  }, [allRows]);

  // Per-department roll-up shown in the sidebar.
  const byDepartment = useMemo(() => {
    const map = new Map<string, number>();
    for (const r of allRows) {
      if (r.department === '—' || r.department === 'Tenant-wide') continue;
      map.set(r.department, (map.get(r.department) ?? 0) + 1);
    }
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [allRows]);

  // Bulk-import dialog state. Re-mounting the InventoryAggregateCard
  // via this nonce key forces it to re-fetch after a successful import.
  const [importOpen, setImportOpen] = useState(false);
  const [aggregateNonce, setAggregateNonce] = useState(0);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">AI Inventory</h1>
          <p className="mt-1 text-sm text-gray-600">
            {ORG.name} — the unified map of every AI tool, model, and
            use case atlas knows about. Joined live from{' '}
            <Link
              href="/dashboard/registry"
              className="font-medium text-primary-600 hover:text-primary-700"
            >
              Registry
            </Link>
            ,{' '}
            <Link
              href="/dashboard/discover"
              className="font-medium text-primary-600 hover:text-primary-700"
            >
              Discover
            </Link>
            , and{' '}
            <Link
              href="/dashboard/integrations"
              className="font-medium text-primary-600 hover:text-primary-700"
            >
              Integrations
            </Link>
            .
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setImportOpen(true)}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Import CSV
          </button>
          <button
            type="button"
            onClick={() => exportCsv(filtered)}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Export CSV ({filtered.length})
          </button>
        </div>
      </div>

      {isDemo && (
        <span className="mt-4 inline-flex rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
          {DEMO_DATA_BANNER}
        </span>
      )}

      {/* Live aggregate from /api/v1/ai-inventory/aggregate. Soft-fails
          to hidden if the backend isn't reachable — rest of the page
          is curated demo data and renders fine without it. */}
      <div className="mt-4">
        <InventoryAggregateCard key={aggregateNonce} />
      </div>

      <CsvImportDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={(r) => {
          // Re-fetch the aggregate so the new counts appear.
          if (r.imported > 0) setAggregateNonce((n) => n + 1);
        }}
      />

      {/* Hero tiles */}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Total inventory
          </p>
          <p className="mt-2 text-3xl font-bold text-gray-900 tabular-nums">
            {counts.total}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Registered
          </p>
          <p className="mt-2 text-3xl font-bold text-emerald-600 tabular-nums">
            {counts.registered}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Shadow
          </p>
          <p className="mt-2 text-3xl font-bold text-amber-600 tabular-nums">
            {counts.shadow}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            High-risk
          </p>
          <p className="mt-2 text-3xl font-bold text-red-600 tabular-nums">
            {counts.high_risk}
          </p>
        </div>
      </div>

      {/* Two-column layout: filters + sidebar (left), table (right) */}
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[260px_1fr]">
        {/* Sidebar */}
        <aside className="space-y-4">
          <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-200">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Source
            </p>
            <div className="mt-2 inline-flex w-full flex-col gap-1">
              {(['all', 'REGISTERED', 'SHADOW', 'SANCTIONED'] as const).map(
                (s) => {
                  const active = sourceFilter === s;
                  return (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setSourceFilter(s)}
                      className={
                        active
                          ? 'rounded-md bg-primary-600 px-3 py-1 text-left text-xs font-semibold text-white'
                          : 'rounded-md px-3 py-1 text-left text-xs font-medium text-gray-700 hover:bg-gray-50'
                      }
                    >
                      {s === 'all' ? 'All sources' : SOURCE_LABEL[s]}
                    </button>
                  );
                },
              )}
            </div>
          </div>

          <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-200">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Risk
            </p>
            <div className="mt-2 inline-flex w-full flex-col gap-1">
              {(['all', 'High', 'Medium', 'Low', 'Unknown'] as const).map(
                (r) => {
                  const active = riskFilter === r;
                  return (
                    <button
                      key={r}
                      type="button"
                      onClick={() => setRiskFilter(r)}
                      className={
                        active
                          ? 'rounded-md bg-primary-600 px-3 py-1 text-left text-xs font-semibold text-white'
                          : 'rounded-md px-3 py-1 text-left text-xs font-medium text-gray-700 hover:bg-gray-50'
                      }
                    >
                      {r === 'all' ? 'All risk levels' : r}
                    </button>
                  );
                },
              )}
            </div>
          </div>

          <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-200">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              By department
            </p>
            <ul className="mt-2 space-y-1 text-xs">
              {byDepartment.slice(0, 8).map(([dept, count]) => (
                <li
                  key={dept}
                  className="flex items-center justify-between gap-2"
                >
                  <span className="truncate text-gray-700">{dept}</span>
                  <span className="rounded-full bg-gray-100 px-1.5 py-0.5 font-medium text-gray-900 tabular-nums">
                    {count}
                  </span>
                </li>
              ))}
              {byDepartment.length === 0 && (
                <li className="text-gray-500">
                  No registered department mapping yet
                </li>
              )}
            </ul>
            <p className="mt-2 text-[11px] text-gray-500">
              Shadow rows aren&apos;t department-tagged until the user
              is identified.
            </p>
          </div>
        </aside>

        {/* Right: search + table */}
        <div>
          <div className="rounded-xl bg-white p-3 shadow-sm ring-1 ring-gray-200">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by title, tool, owner, department…"
              className="w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </div>

          <div className="mt-3 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-100 text-sm">
                <thead>
                  <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                    <th className="px-4 py-2">Title</th>
                    <th className="px-4 py-2">Tool</th>
                    <th className="px-4 py-2">Source</th>
                    <th className="px-4 py-2">Department</th>
                    <th className="px-4 py-2">Owner</th>
                    <th className="px-4 py-2">Risk</th>
                    <th className="px-4 py-2">Last seen</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {loading ? (
                    <tr>
                      <td
                        colSpan={7}
                        className="px-4 py-8 text-center text-sm text-gray-500"
                      >
                        Loading AI assets…
                      </td>
                    </tr>
                  ) : (
                    filtered.map((row) => (
                    <tr key={row.key} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900">
                          {row.title}
                        </div>
                        <div className="text-xs text-gray-500">{row.meta}</div>
                      </td>
                      <td className="px-4 py-3 text-gray-700">{row.tool}</td>
                      <td className="px-4 py-3">
                        <SourceChip source={row.source} />
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-700">
                        {row.department}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-700">
                        {row.owner}
                      </td>
                      <td className="px-4 py-3">
                        <RiskChip tier={row.riskTier} />
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                        {row.lastObservedAt}
                      </td>
                    </tr>
                    ))
                  )}
                  {!loading && filtered.length === 0 && (
                    <tr>
                      <td
                        colSpan={7}
                        className="px-4 py-8 text-center text-sm text-gray-500"
                      >
                        {allRows.length === 0 ? (
                          <>
                            No AI assets registered yet.{' '}
                            <Link
                              href="/dashboard/register"
                              className="font-medium text-primary-600 hover:text-primary-700"
                            >
                              Register a use case →
                            </Link>
                            {error && <span className="block pt-2 text-red-600">{error}</span>}
                          </>
                        ) : (
                          <>
                            No rows match the current filter. Adjust the filters or{' '}
                            <button
                              type="button"
                              onClick={() => {
                                setSourceFilter('all');
                                setRiskFilter('all');
                                setSearch('');
                              }}
                              className="font-medium text-primary-600 hover:text-primary-700"
                            >
                              reset
                            </button>
                            .
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <p className="mt-3 text-xs text-gray-500">
            <strong>Heuristic note.</strong> Risk tier on
            shadow-detected rows comes from the Defender risk score;
            registered rows use the IT-lead-assigned tier; sanctioned
            rows infer risk from the model name lookup against{' '}
            <Link
              href="/dashboard/model-risk"
              className="font-medium text-primary-600 hover:text-primary-700"
            >
              Model Risk
            </Link>
            . v0.2 backend endpoint{' '}
            <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
              /api/v1/ai-inventory
            </code>{' '}
            will join these properly.
          </p>
        </div>
      </div>
    </div>
  );
}
