'use client';

// ─────────────────────────────────────────────────────────────────────
// Use-case registry — atlas dashboard, new page
//
// Companion to docs/use-case-survey-bot.md.
//
// Role:
//   The catalogue of registered AI use cases. The destination for the
//   bot survey (the acquisition channel) and the §3.7 Register
//   form-driven path. Compliance / IT-lead reviews here, risk-rates,
//   assigns owner, promotes Review→Active.
//
// Sections:
//   1. Header with status counts + "Send survey" button
//   2. Registered use cases table
//   3. Recent surveys card (history + completion % per delivery)
//   4. Footer cross-link to /discover (Shadow AI promote pathway)
//
// The "Send survey" modal collects audience + template + channel and
// toasts the simulated dispatch — same UX pattern as Policies §3.4.
// In production this POSTs /api/v1/surveys/dispatches which calls
// the Slack delivery worker; today it's a setState + toast.
//
// Data:
//   REGISTERED_USE_CASES, USE_CASE_SURVEY_TEMPLATE,
//   RECENT_SURVEY_DELIVERIES, DEPARTMENTS — all from curated-demo-
//   data.ts. Swap-in for /api/v1/use-cases when M2 routers ship.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import {
  REGISTERED_USE_CASES,
  USE_CASE_SURVEY_TEMPLATE,
  RECENT_SURVEY_DELIVERIES,
  DEPARTMENTS,
  ORG,
  type RegisteredUseCase,
  type UseCaseStatus,
  type UseCaseRiskTier,
  type UseCaseSource,
  type SurveyDeliverySummary,
} from '@/lib/curated-demo-data';
import type {
  DispatchResponse,
  UseCaseResponse,
} from '@/lib/types';

// ─── Server→curated adapters ─────────────────────────────────────────
//
// Backend returns SCREAMING_CASE enums and snake_case fields; the UI
// is built around the curated shape with Title-cased values. These
// adapters keep the renderers unchanged.

const STATUS_SERVER_TO_CURATED: Record<string, UseCaseStatus> = {
  DRAFT: 'Draft',
  REVIEW: 'Review',
  ACTIVE: 'Active',
  RETIRED: 'Retired',
};

const RISK_SERVER_TO_CURATED: Record<string, UseCaseRiskTier> = {
  LOW: 'Low',
  MEDIUM: 'Medium',
  HIGH: 'High',
};

const SOURCE_SERVER_TO_CURATED: Record<string, UseCaseSource> = {
  BOT: 'bot',
  FORM: 'form',
  SHADOW_PROMOTE: 'shadow-promote',
};

function serverToUseCase(r: UseCaseResponse): RegisteredUseCase {
  return {
    id: r.id,
    title: r.title,
    tool: r.tool,
    department: r.department,
    // Backend stores owner_user_id; we don't yet join to display name.
    // Display the short uuid for now; the per-row drawer (follow-up)
    // hydrates the full user.
    owner: r.owner_user_id ? `…${r.owner_user_id.slice(-8)}` : '—',
    status: STATUS_SERVER_TO_CURATED[r.status] ?? 'Draft',
    riskTier: RISK_SERVER_TO_CURATED[r.risk_tier] ?? 'Low',
    source: SOURCE_SERVER_TO_CURATED[r.source] ?? 'form',
    dataClasses: r.data_classes,
    registeredAt: r.created_at.slice(0, 10),
  };
}

function serverToDispatch(d: DispatchResponse): SurveyDeliverySummary {
  return {
    id: d.id,
    name: d.name,
    sentAt: (d.dispatched_at ?? d.created_at).slice(0, 10),
    audienceLabel: d.audience_label,
    recipientCount: d.recipient_count,
    completedCount: d.completed_count,
    newRegistrationCount: d.new_registration_count,
    channel: d.channel === 'EMAIL' ? 'email' : 'slack',
  };
}

// ─── Chips ────────────────────────────────────────────────────────────

const STATUS_CLASSES: Record<UseCaseStatus, string> = {
  Active: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  Review: 'bg-amber-50 text-amber-700 ring-amber-200',
  Draft: 'bg-gray-100 text-gray-700 ring-gray-200',
  Retired: 'bg-gray-50 text-gray-500 ring-gray-200',
};

function StatusChip({ status }: { status: UseCaseStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_CLASSES[status]}`}
    >
      {status}
    </span>
  );
}

const RISK_CLASSES: Record<UseCaseRiskTier, string> = {
  Low: 'bg-gray-100 text-gray-700',
  Medium: 'bg-amber-50 text-amber-700',
  High: 'bg-red-50 text-red-700',
};

function RiskChip({ tier }: { tier: UseCaseRiskTier }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${RISK_CLASSES[tier]}`}
    >
      {tier}
    </span>
  );
}

const SOURCE_LABELS: Record<UseCaseSource, string> = {
  bot: '🤖 Bot',
  form: '📝 Form',
  'shadow-promote': '🔭 Shadow → Active',
};

function SourceLabel({ source }: { source: UseCaseSource }) {
  return (
    <span className="text-[11px] text-gray-600" title={`Registered via ${source}`}>
      {SOURCE_LABELS[source]}
    </span>
  );
}

// ─── Use-case table ───────────────────────────────────────────────────

function UseCaseTable({ rows }: { rows: RegisteredUseCase[] }) {
  return (
    <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-100 text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-4 py-2">Title</th>
              <th className="px-4 py-2">Tool</th>
              <th className="px-4 py-2">Department</th>
              <th className="px-4 py-2">Owner</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Risk</th>
              <th className="px-4 py-2">Source</th>
              <th className="px-4 py-2">Registered</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-4 py-8 text-center text-sm text-gray-500"
                >
                  No use cases registered yet. Send a survey to seed the
                  registry.
                </td>
              </tr>
            )}
            {rows.map((u) => (
              <tr key={u.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-gray-900">
                  {u.title}
                </td>
                <td className="px-4 py-3 text-gray-700">{u.tool}</td>
                <td className="px-4 py-3 text-gray-700">{u.department}</td>
                <td className="whitespace-nowrap px-4 py-3 text-gray-700">
                  {u.owner}
                </td>
                <td className="px-4 py-3">
                  <StatusChip status={u.status} />
                </td>
                <td className="px-4 py-3">
                  <RiskChip tier={u.riskTier} />
                </td>
                <td className="px-4 py-3">
                  <SourceLabel source={u.source} />
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                  {u.registeredAt}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Recent surveys card ──────────────────────────────────────────────

function pct(n: number, d: number): number {
  return d > 0 ? Math.round((n / d) * 100) : 0;
}

function RecentSurveys({ deliveries }: { deliveries: SurveyDeliverySummary[] }) {
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Recent surveys</h3>
        <span className="text-xs text-gray-500">
          {deliveries.length} dispatches
        </span>
      </div>
      <ul className="mt-4 space-y-3">
        {deliveries.map((d) => {
          const completion = pct(d.completedCount, d.recipientCount);
          return (
            <li
              key={d.id}
              className="rounded-md bg-gray-50 px-4 py-3 ring-1 ring-gray-100"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-gray-900">{d.name}</span>
                <span className="text-xs text-gray-500">
                  {d.sentAt} ·{' '}
                  {d.channel === 'slack' ? 'Slack DM' : 'Email'}
                </span>
              </div>
              <p className="mt-1 text-xs text-gray-600">{d.audienceLabel}</p>
              <div className="mt-2 grid grid-cols-3 gap-3 text-xs">
                <div>
                  <p className="text-gray-500">Sent</p>
                  <p className="font-semibold text-gray-900 tabular-nums">
                    {d.recipientCount}
                  </p>
                </div>
                <div>
                  <p className="text-gray-500">Completed</p>
                  <p className="font-semibold text-gray-900 tabular-nums">
                    {d.completedCount} ({completion}%)
                  </p>
                </div>
                <div>
                  <p className="text-gray-500">New use cases</p>
                  <p className="font-semibold text-emerald-700 tabular-nums">
                    +{d.newRegistrationCount}
                  </p>
                </div>
              </div>
              <div className="mt-2 h-1.5 w-full rounded-full bg-gray-200">
                <div
                  className="h-1.5 rounded-full bg-emerald-500"
                  style={{ width: `${completion}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 text-xs text-gray-500">
        Send a reminder to incomplete responders from the per-delivery
        drill-down (lands when the bot worker ships, see{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
          docs/use-case-survey-bot.md
        </code>{' '}
        §5.3).
      </p>
    </div>
  );
}

// ─── Send-survey modal ────────────────────────────────────────────────

type AudienceMode = 'all' | 'departments' | 'tools' | 'custom';

interface SurveyDraft {
  templateId: string;
  audienceMode: AudienceMode;
  selectedDepts: string[];
  selectedTools: string[];
  customList: string; // newline-separated
  channel: 'slack' | 'email';
}

const EMPTY_DRAFT: SurveyDraft = {
  templateId: USE_CASE_SURVEY_TEMPLATE.id,
  audienceMode: 'all',
  selectedDepts: [],
  selectedTools: [],
  customList: '',
  channel: 'slack',
};

const KNOWN_TOOLS = [
  'Microsoft Copilot',
  'ChatGPT',
  'Claude',
  'Gemini',
  'Perplexity',
];

function recipientCountFor(draft: SurveyDraft): number {
  switch (draft.audienceMode) {
    case 'all':
      return ORG.totalUsers;
    case 'departments':
      return draft.selectedDepts.reduce((acc, d) => {
        const dept = DEPARTMENTS.find((x) => x.name === d);
        return acc + (dept?.users ?? 0);
      }, 0);
    case 'tools':
      // Curated proxy: each detected tool maps to its share-of-prompts %
      // of ORG.totalUsers. Estimates the audience size.
      // (In production this hits the discover service.)
      if (draft.selectedTools.length === 0) return 0;
      // 1 user can appear in N tool buckets — keep this simple and
      // count unique-ish via the rough rule: 1 tool ≈ 18 users,
      // 2 tools ≈ 28, 3+ ≈ 40.
      const n = draft.selectedTools.length;
      return Math.min(ORG.totalUsers, 12 + n * 8);
    case 'custom':
      return draft.customList.split(/[\n,]/).filter((s) => s.trim()).length;
    default:
      return 0;
  }
}

function SendSurveyModal({
  open,
  onClose,
  onDispatch,
}: {
  open: boolean;
  onClose: () => void;
  onDispatch: (d: SurveyDraft, count: number) => void;
}) {
  const [draft, setDraft] = useState<SurveyDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<string | null>(null);
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    if (open) {
      setDraft(EMPTY_DRAFT);
      setError(null);
      const id = window.setTimeout(() => firstFieldRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const recipients = recipientCountFor(draft);

  const submit = () => {
    if (draft.audienceMode === 'departments' && draft.selectedDepts.length === 0) {
      setError('Pick at least one department');
      return;
    }
    if (draft.audienceMode === 'tools' && draft.selectedTools.length === 0) {
      setError('Pick at least one tool');
      return;
    }
    if (draft.audienceMode === 'custom' && recipients === 0) {
      setError('Paste at least one email or Slack handle');
      return;
    }
    onDispatch(draft, recipients);
  };

  const toggle = (
    arrKey: 'selectedDepts' | 'selectedTools',
    item: string,
  ) => {
    setDraft((d) => ({
      ...d,
      [arrKey]: d[arrKey].includes(item)
        ? d[arrKey].filter((x) => x !== item)
        : [...d[arrKey], item],
    }));
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="send-survey-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-xl rounded-xl bg-white p-6 shadow-xl ring-1 ring-gray-200">
        <div className="flex items-center justify-between">
          <h2
            id="send-survey-title"
            className="text-lg font-semibold text-gray-900"
          >
            Send survey
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        <div className="mt-4 space-y-4">
          {/* Template */}
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Template
            </span>
            <select
              ref={firstFieldRef}
              value={draft.templateId}
              onChange={(e) =>
                setDraft((d) => ({ ...d, templateId: e.target.value }))
              }
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value={USE_CASE_SURVEY_TEMPLATE.id}>
                {USE_CASE_SURVEY_TEMPLATE.name} (
                {USE_CASE_SURVEY_TEMPLATE.version})
              </option>
            </select>
            <p className="mt-1 text-[11px] text-gray-500">
              {USE_CASE_SURVEY_TEMPLATE.description}{' '}
              <span className="text-gray-600">
                {USE_CASE_SURVEY_TEMPLATE.questions.length} questions.
              </span>
            </p>
          </label>

          {/* Audience */}
          <fieldset>
            <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Audience
            </legend>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {(
                [
                  ['all', 'All staff'],
                  ['departments', 'By department'],
                  ['tools', 'By tool'],
                  ['custom', 'Custom list'],
                ] as const
              ).map(([key, label]) => {
                const active = draft.audienceMode === key;
                return (
                  <button
                    type="button"
                    key={key}
                    onClick={() =>
                      setDraft((d) => ({ ...d, audienceMode: key }))
                    }
                    className={
                      active
                        ? 'rounded-md border border-primary-500 bg-primary-50 px-3 py-2 text-xs font-semibold text-primary-900 ring-1 ring-primary-200'
                        : 'rounded-md border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 hover:border-gray-300'
                    }
                  >
                    {label}
                  </button>
                );
              })}
            </div>

            {draft.audienceMode === 'departments' && (
              <div className="mt-3 max-h-44 overflow-y-auto rounded-md border border-gray-300 bg-white p-2">
                {DEPARTMENTS.map((d) => (
                  <label
                    key={d.name}
                    className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-gray-50"
                  >
                    <input
                      type="checkbox"
                      checked={draft.selectedDepts.includes(d.name)}
                      onChange={() => toggle('selectedDepts', d.name)}
                      className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                    />
                    <span className="text-sm text-gray-800">{d.name}</span>
                    <span className="ml-auto text-xs text-gray-500">
                      {d.users} users
                    </span>
                  </label>
                ))}
              </div>
            )}

            {draft.audienceMode === 'tools' && (
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                {KNOWN_TOOLS.map((t) => (
                  <label
                    key={t}
                    className="flex cursor-pointer items-center gap-2 rounded-md border border-gray-200 bg-white px-2 py-1.5 text-sm hover:border-gray-300"
                  >
                    <input
                      type="checkbox"
                      checked={draft.selectedTools.includes(t)}
                      onChange={() => toggle('selectedTools', t)}
                      className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                    />
                    <span className="text-gray-800">{t}</span>
                  </label>
                ))}
                <p className="col-span-full text-[11px] text-gray-500">
                  Audience reads from <em>Promptly endpoint detections</em>{' '}
                  for the selected tools.
                </p>
              </div>
            )}

            {draft.audienceMode === 'custom' && (
              <textarea
                value={draft.customList}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, customList: e.target.value }))
                }
                placeholder="One email or Slack handle per line"
                rows={4}
                className="mt-3 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            )}

            <p className="mt-2 text-xs text-gray-500">
              Estimated recipients:{' '}
              <span className="font-semibold text-gray-900">
                {recipients}
              </span>
            </p>
          </fieldset>

          {/* Channel */}
          <fieldset>
            <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Delivery channel
            </legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(['slack', 'email'] as const).map((c) => {
                const active = draft.channel === c;
                return (
                  <button
                    type="button"
                    key={c}
                    onClick={() => setDraft((d) => ({ ...d, channel: c }))}
                    className={
                      active
                        ? 'rounded-md border border-primary-500 bg-primary-50 px-3 py-2 text-left ring-1 ring-primary-200'
                        : 'rounded-md border border-gray-200 bg-white px-3 py-2 text-left hover:border-gray-300'
                    }
                  >
                    <p className="text-sm font-medium text-gray-900">
                      {c === 'slack' ? 'Slack DM' : 'Email fallback'}
                    </p>
                    <p className="mt-0.5 text-[11px] text-gray-600">
                      {c === 'slack'
                        ? 'Block Kit interactive — fastest completion'
                        : 'Magic-link to hosted survey — users without Slack'}
                    </p>
                  </button>
                );
              })}
            </div>
          </fieldset>

          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
              {error}
            </p>
          )}
        </div>

        <div className="mt-6 flex items-center justify-between gap-3 border-t border-gray-100 pt-4">
          <p className="text-xs text-gray-500">
            Recipients see a consent header + STOP opt-out. See the bot
            doc §6.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submit}
              className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
            >
              Dispatch
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Toast ────────────────────────────────────────────────────────────

function Toast({
  message,
  onDone,
}: {
  message: string;
  onDone: () => void;
}) {
  useEffect(() => {
    const id = window.setTimeout(onDone, 4500);
    return () => window.clearTimeout(id);
  }, [onDone]);
  return (
    <div
      className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-xl"
      role="status"
    >
      {message}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function RegistryPage() {
  const [modalOpen, setModalOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [extraDeliveries, setExtraDeliveries] = useState<
    SurveyDeliverySummary[]
  >([]);

  // Live data — null until loaded; null = use curated fallback so
  // the demo flow still works without a backend.
  const [serverUseCases, setServerUseCases] = useState<
    RegisteredUseCase[] | null
  >(null);
  const [serverDispatches, setServerDispatches] = useState<
    SurveyDeliverySummary[] | null
  >(null);

  // Mount: try live API for use cases + recent dispatches. Soft-fail.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await api.listUseCases({ page_size: 100 });
        if (!cancelled) {
          setServerUseCases(res.use_cases.map(serverToUseCase));
        }
      } catch {
        /* keep curated fallback */
      }

      try {
        const res = await api.listSurveyDispatches();
        if (!cancelled) {
          setServerDispatches(res.dispatches.map(serverToDispatch));
        }
      } catch {
        /* keep curated fallback */
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Live + curated fallbacks for the table and the dispatches strip.
  const useCases = serverUseCases ?? REGISTERED_USE_CASES;
  const dispatches = useMemo(() => {
    const baseline = serverDispatches ?? RECENT_SURVEY_DELIVERIES;
    return [...extraDeliveries, ...baseline];
  }, [extraDeliveries, serverDispatches]);

  const counts = useMemo(() => {
    const c: Record<UseCaseStatus, number> = {
      Active: 0,
      Review: 0,
      Draft: 0,
      Retired: 0,
    };
    for (const u of useCases) c[u.status] += 1;
    return c;
  }, [useCases]);

  /**
   * Send survey handler — tries the live `/api/v1/surveys/dispatches`
   * endpoint first; falls back to the in-memory simulation when no
   * backend is reachable.
   *
   * Audience filter shape is converted from the modal's `SurveyDraft`
   * (a UI-shaped object) into the backend's AudienceFilter.
   */
  const handleDispatch = async (d: SurveyDraft, count: number) => {
    const today = new Date().toISOString().slice(0, 10);
    let audienceLabel = 'All staff';
    let serverValues: string[] = [];
    let serverMode: 'all' | 'departments' | 'tools' | 'custom' = 'all';
    if (d.audienceMode === 'departments') {
      audienceLabel = `${d.selectedDepts.length} departments`;
      serverMode = 'departments';
      serverValues = d.selectedDepts;
    } else if (d.audienceMode === 'tools') {
      audienceLabel = `Detected on ${d.selectedTools.join(', ')}`;
      serverMode = 'tools';
      serverValues = d.selectedTools;
    } else if (d.audienceMode === 'custom') {
      audienceLabel = 'Custom list';
      serverMode = 'custom';
      serverValues = d.customList
        .split(/[\n,]/)
        .map((s) => s.trim())
        .filter(Boolean);
    }

    // Optimistic local row so the user sees feedback even before
    // the network call completes / if it fails.
    const optimistic: SurveyDeliverySummary = {
      id: `sd-pending-${Date.now()}`,
      name: `${USE_CASE_SURVEY_TEMPLATE.name} — ${today}`,
      sentAt: today,
      audienceLabel: `${audienceLabel} (${count})`,
      recipientCount: count,
      completedCount: 0,
      newRegistrationCount: 0,
      channel: d.channel,
    };
    setExtraDeliveries((arr) => [optimistic, ...arr]);
    setModalOpen(false);
    const channelLabel = d.channel === 'slack' ? 'Slack DM' : 'email';

    try {
      // Best-effort seed so the foundation template exists for the
      // tenant. Idempotent — no-op when already seeded.
      try {
        await api.seedDefaultSurveyTemplate();
      } catch {
        /* if seeding fails the dispatch will throw with a clearer error */
      }

      const created = await api.dispatchSurvey({
        template_slug: USE_CASE_SURVEY_TEMPLATE.id.replace(/-v\d+$/, ''),
        template_version: USE_CASE_SURVEY_TEMPLATE.version,
        audience: { mode: serverMode, values: serverValues },
        channel: d.channel === 'email' ? 'EMAIL' : 'SLACK',
      });

      // Replace the optimistic row with the server's authoritative row.
      const live = serverToDispatch(created);
      setExtraDeliveries((arr) =>
        arr.map((row) => (row.id === optimistic.id ? live : row)),
      );
      setToast(
        `✓ Survey dispatched to ${created.recipient_count} recipients via ${channelLabel}`,
      );
    } catch {
      // Live dispatch failed — keep the optimistic local row so the
      // demo flow stays intact, mention the soft-fail in the toast.
      setToast(
        `✓ Survey queued for ${count} recipients via ${channelLabel} (demo)`,
      );
    }
  };

  return (
    <div>
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Use-case registry
          </h1>
          <p className="mt-1 text-sm text-gray-600">
            {ORG.name} — {counts.Active} active · {counts.Review} under
            review · {counts.Draft} draft
          </p>
        </div>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
        >
          Send survey
        </button>
      </div>

      {/* Use case table */}
      <div className="mt-6">
        <UseCaseTable rows={useCases} />
      </div>

      {/* Recent surveys */}
      <div className="mt-6">
        <RecentSurveys deliveries={dispatches} />
      </div>

      {/* Footer cross-link */}
      <p className="mt-4 text-xs text-gray-500">
        Use cases registered from a <em>shadow promotion</em> link back
        to{' '}
        <Link
          href="/dashboard/feed?event=Coached"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          the Activity Log
        </Link>
        . Bot setup + question flow lives in{' '}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px] text-gray-700">
          docs/use-case-survey-bot.md
        </code>
        .
      </p>

      <SendSurveyModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onDispatch={handleDispatch}
      />
      {toast && <Toast message={toast} onDone={() => setToast(null)} />}
    </div>
  );
}
