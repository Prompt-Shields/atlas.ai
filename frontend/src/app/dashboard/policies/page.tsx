'use client';

// ─────────────────────────────────────────────────────────────────────
// Policies — atlas dashboard §3.4 — the click-and-impress moment
//
// Role:
//   Customer creates a policy, sees it pushed via Intune, dashboard
//   accepts. This is the closer page on every demo.
//
// Two presentations, toggled at the top:
//
//   • Simple (default, IT-lead audience):
//       - List view of curated POLICIES + "+ New policy" modal
//       - 4 fields: name, scope, rule (redact/coach/block), sensitive
//         type
//       - Save toasts "Pushed to N endpoints via Intune" where N is
//         the sum of users in selected scope departments
//
//   • Advanced (DPO / Security Lead audience):
//       - Links to the heavyweight template + instance system that
//         ships in PR #5 (mode-toggle, promotion-wizard,
//         policy-types, policy-helpers)
//       - When PR #5 lands and the /policies/templates +
//         /policies/<id> routes exist, this becomes a richer
//         landing strip
//
// Why both: an IT lead at a pilot organisation needs to push a rule
// via Intune in 30 seconds. A regulated insurer's DPO needs the
// Guideline ↔ Strict lifecycle with watchdog gating. The toggle
// satisfies both without making either feel cluttered.
//
// State:
//   The newly-created policy is held in component memory only —
//   refresh clears the demo. When cursor's M2 routers ship, swap
//   newPolicies + setNewPolicies for an api.createPolicy() call
//   and a refetch. JSX doesn't change.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  DEMO_DATA_BANNER,
  POLICIES,
  DEPARTMENTS,
  ORG,
  type CuratedPolicy,
  type PolicyRule,
  type PolicyStatus,
} from '@/lib/curated-demo-data';
import { isDemoFallbackEnabled } from '@/lib/demo-mode';

// ─── Sub-components ───────────────────────────────────────────────────

const RULE_CLASSES: Record<PolicyRule, string> = {
  redact: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  coach: 'bg-purple-50 text-purple-700 ring-purple-200',
  block: 'bg-red-50 text-red-700 ring-red-200',
};

const RULE_DESCRIPTIONS: Record<PolicyRule, string> = {
  redact: 'Replace with placeholder before send',
  coach: 'Show in-line nudge, allow send',
  block: 'Block submission, log event',
};

function RuleChip({ rule }: { rule: PolicyRule }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${RULE_CLASSES[rule]}`}
    >
      {rule}
    </span>
  );
}

const STATUS_CLASSES: Record<PolicyStatus, string> = {
  Active: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  Draft: 'bg-gray-100 text-gray-700 ring-gray-200',
};

function StatusChip({ status }: { status: PolicyStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_CLASSES[status]}`}
    >
      {status}
    </span>
  );
}

function ScopeList({ scope }: { scope: readonly string[] }) {
  if (scope.length <= 2) {
    return <span className="text-xs text-gray-700">{scope.join(', ')}</span>;
  }
  return (
    <span className="text-xs text-gray-700">
      {scope.slice(0, 2).join(', ')}
      <span className="text-gray-500"> +{scope.length - 2} more</span>
    </span>
  );
}

// ─── Mode toggle ──────────────────────────────────────────────────────

type Mode = 'simple' | 'advanced';

function ModeToggle({
  mode,
  onChange,
}: {
  mode: Mode;
  onChange: (m: Mode) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-gray-200 bg-white p-0.5 shadow-sm">
      {(['simple', 'advanced'] as const).map((m) => {
        const active = m === mode;
        return (
          <button
            key={m}
            type="button"
            onClick={() => onChange(m)}
            className={
              active
                ? 'rounded-md bg-primary-600 px-3 py-1 text-xs font-semibold text-white'
                : 'rounded-md px-3 py-1 text-xs font-medium text-gray-600 hover:text-gray-900'
            }
          >
            {m === 'simple' ? 'Simple' : 'Advanced'}
          </button>
        );
      })}
    </div>
  );
}

// ─── Simple view ──────────────────────────────────────────────────────

function PolicyTable({ policies }: { policies: CuratedPolicy[] }) {
  return (
    <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-100 text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-4 py-2">Name</th>
              <th className="px-4 py-2">Scope</th>
              <th className="px-4 py-2">Rule</th>
              <th className="px-4 py-2">Sensitive type</th>
              <th className="px-4 py-2">Last updated</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {policies.map((p) => (
              <tr key={p.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-gray-900">
                  {p.name}
                </td>
                <td className="px-4 py-3">
                  <ScopeList scope={p.scope} />
                </td>
                <td className="px-4 py-3" title={RULE_DESCRIPTIONS[p.rule]}>
                  <RuleChip rule={p.rule} />
                </td>
                <td className="px-4 py-3 text-xs text-gray-600">
                  {p.sensitiveType}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                  {p.lastUpdated}
                </td>
                <td className="px-4 py-3">
                  <StatusChip status={p.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── New-policy modal ─────────────────────────────────────────────────

interface NewPolicyDraft {
  name: string;
  scope: string[];
  rule: PolicyRule;
  sensitiveType: string;
}

const EMPTY_DRAFT: NewPolicyDraft = {
  name: '',
  scope: [],
  rule: 'redact',
  sensitiveType: '',
};

function endpointsForScope(scope: string[]): number {
  return scope.reduce((acc, deptName) => {
    const d = DEPARTMENTS.find((x) => x.name === deptName);
    return acc + (d?.users ?? 0);
  }, 0);
}

function NewPolicyModal({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (p: CuratedPolicy) => void;
}) {
  const [draft, setDraft] = useState<NewPolicyDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<string | null>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  // Reset state when reopened.
  useEffect(() => {
    if (open) {
      setDraft(EMPTY_DRAFT);
      setError(null);
      // Focus the first field on the next tick.
      const id = window.setTimeout(() => firstFieldRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [open]);

  // Escape closes.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const toggleScope = (deptName: string) => {
    setDraft((d) => ({
      ...d,
      scope: d.scope.includes(deptName)
        ? d.scope.filter((s) => s !== deptName)
        : [...d.scope, deptName],
    }));
  };

  const submit = () => {
    if (!draft.name.trim()) {
      setError('Policy name is required');
      return;
    }
    if (draft.scope.length === 0) {
      setError('Pick at least one department');
      return;
    }
    if (!draft.sensitiveType.trim()) {
      setError('Sensitive type is required');
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    onCreate({
      id: `pol-${Date.now()}`,
      name: draft.name.trim(),
      scope: draft.scope,
      rule: draft.rule,
      sensitiveType: draft.sensitiveType.trim(),
      status: 'Active',
      lastUpdated: today,
    });
  };

  const endpoints = endpointsForScope(draft.scope);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-policy-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-xl ring-1 ring-gray-200">
        <div className="flex items-center justify-between">
          <h2
            id="new-policy-title"
            className="text-lg font-semibold text-gray-900"
          >
            New policy
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
          {/* Name */}
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Name
            </span>
            <input
              ref={firstFieldRef}
              type="text"
              value={draft.name}
              onChange={(e) =>
                setDraft((d) => ({ ...d, name: e.target.value }))
              }
              placeholder="No PHI to external LLMs"
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>

          {/* Scope */}
          <fieldset>
            <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Scope · departments
            </legend>
            <div className="mt-1 max-h-44 overflow-y-auto rounded-md border border-gray-300 bg-white p-2">
              {DEPARTMENTS.map((d) => (
                <label
                  key={d.name}
                  className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    checked={draft.scope.includes(d.name)}
                    onChange={() => toggleScope(d.name)}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                  />
                  <span className="text-sm text-gray-800">{d.name}</span>
                  <span className="ml-auto text-xs text-gray-500">
                    {d.users} users
                  </span>
                </label>
              ))}
            </div>
            <p className="mt-1 text-xs text-gray-500">
              {draft.scope.length === 0
                ? 'Pick one or more departments.'
                : `${draft.scope.length} selected · ${endpoints} endpoints`}
            </p>
          </fieldset>

          {/* Rule */}
          <fieldset>
            <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Rule
            </legend>
            <div className="mt-1 grid grid-cols-3 gap-2">
              {(['redact', 'coach', 'block'] as const).map((r) => {
                const active = draft.rule === r;
                return (
                  <button
                    type="button"
                    key={r}
                    onClick={() => setDraft((d) => ({ ...d, rule: r }))}
                    className={
                      active
                        ? 'rounded-md border border-primary-500 bg-primary-50 px-3 py-2 text-left ring-1 ring-primary-200'
                        : 'rounded-md border border-gray-200 bg-white px-3 py-2 text-left hover:border-gray-300'
                    }
                  >
                    <div className="flex items-center gap-2">
                      <RuleChip rule={r} />
                    </div>
                    <p className="mt-1 text-[11px] text-gray-600">
                      {RULE_DESCRIPTIONS[r]}
                    </p>
                  </button>
                );
              })}
            </div>
          </fieldset>

          {/* Sensitive type */}
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Sensitive type
            </span>
            <input
              type="text"
              value={draft.sensitiveType}
              onChange={(e) =>
                setDraft((d) => ({ ...d, sensitiveType: e.target.value }))
              }
              placeholder="PHI · PII · Compensation · Protected characteristics …"
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>

          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
              {error}
            </p>
          )}
        </div>

        <div className="mt-6 flex items-center justify-between gap-3 border-t border-gray-100 pt-4">
          <p className="text-xs text-gray-500">
            Saving pushes the rule to{' '}
            <span className="font-semibold text-gray-700">
              {endpoints}
            </span>{' '}
            endpoints via Intune.
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
              Save & push
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
    const id = window.setTimeout(onDone, 4000);
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

// ─── Advanced view ────────────────────────────────────────────────────

function AdvancedView() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Link
        href="/dashboard/policies/templates"
        className="block rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200 transition hover:ring-primary-300"
      >
        <h3 className="text-sm font-semibold text-gray-900">
          Template library →
        </h3>
        <p className="mt-2 text-sm text-gray-600">
          Atlas&apos;s policy-template catalogue: OWASP LLM, EU AI Act, GDPR,
          industry packs, content safety, shadow AI. Clone a template
          into a tenant-scoped <em>instance</em> to start observing.
        </p>
        <p className="mt-3 text-xs text-gray-500">
          Renders once PR #5&apos;s <code>/policies/templates</code> route
          lands.
        </p>
      </Link>

      <Link
        href="/dashboard/policies/instances"
        className="block rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200 transition hover:ring-primary-300"
      >
        <h3 className="text-sm font-semibold text-gray-900">
          Active instances →
        </h3>
        <p className="mt-2 text-sm text-gray-600">
          Per-instance Guideline ↔ Strict promotion wizard, eligibility
          checklist (false-positive rate, watchdog dwell time, approver
          sign-off), and the demote-to-Guideline rollback control.
        </p>
        <p className="mt-3 text-xs text-gray-500">
          Wraps PR #5&apos;s <code>PolicyModeToggle</code> +{' '}
          <code>PromotionWizard</code>.
        </p>
      </Link>

      <div className="md:col-span-2 rounded-xl bg-amber-50 px-5 py-4 ring-1 ring-amber-100">
        <p className="text-sm text-amber-900">
          <strong>Advanced is a power-user surface.</strong> If your team
          isn&apos;t running a Guideline-first observation period before
          enforcement, stay on <em>Simple</em> — push the rule and watch
          the Activity Log. Advanced unlocks for tenants with a DPO or
          Security Lead persona enabled.
        </p>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function PoliciesPage() {
  const [mode, setMode] = useState<Mode>('simple');
  const [modalOpen, setModalOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [extras, setExtras] = useState<CuratedPolicy[]>([]);

  const policies = useMemo(
    () => [...extras, ...POLICIES],
    [extras],
  );

  const isDemoEnabled = isDemoFallbackEnabled();

  const handleCreate = (p: CuratedPolicy) => {
    setExtras((arr) => [p, ...arr]);
    setModalOpen(false);
    const endpoints = endpointsForScope(p.scope);
    setToast(`✓ "${p.name}" pushed to ${endpoints} endpoints via Intune`);
  };

  if (!isDemoEnabled) {
    return (
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h1 className="text-2xl font-bold text-gray-900">Policies</h1>
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
          <h1 className="text-2xl font-bold text-gray-900">Policies</h1>
          <p className="mt-1 text-sm text-gray-600">
            {ORG.name} —{' '}
            {mode === 'simple'
              ? 'push and observe via Intune'
              : 'Guideline ↔ Strict lifecycle, approval-gated promotion'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ModeToggle mode={mode} onChange={setMode} />
          {mode === 'simple' && (
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
            >
              + New policy
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="mt-6">
        {mode === 'simple' ? (
          <PolicyTable policies={policies} />
        ) : (
          <AdvancedView />
        )}
      </div>

      {/* Footer cross-link */}
      {mode === 'simple' && (
        <p className="mt-4 text-xs text-gray-500">
          Every triggered policy emits an event in the{' '}
          <Link
            href="/dashboard/feed"
            className="font-medium text-primary-600 hover:text-primary-700"
          >
            Activity Log
          </Link>
          . Severity is set on the rule; redact / coach / block tints
          match the event pills there.
        </p>
      )}

      {/* Modal */}
      <NewPolicyModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreate={handleCreate}
      />

      {/* Toast */}
      {toast && <Toast message={toast} onDone={() => setToast(null)} />}
    </div>
  );
}
