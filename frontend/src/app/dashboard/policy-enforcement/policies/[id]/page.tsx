'use client';

// ─────────────────────────────────────────────────────────────────────
// Policy instance detail — /dashboard/policy-enforcement/policies/[id]
// (M2, issue #240)
//
// Shows the instance's inherited rules (via its template), its 30-day
// violation stats + promotion/demotion history, and supports
// promote (Guideline → Strict, gated by eligibility/approvers) and
// demote (Strict → Guideline, always permitted).
//
// GAP: the backend (#237/#238) writes individual `PolicyViolation` rows
// on every blocked evaluation but exposes no `GET /pep/policies/{id}/
// violations` list endpoint yet — only the rolling `stats` counters and
// the append-only `promotion_history` log on the instance itself. So
// "violation history" here surfaces that aggregate/event data, same
// convention as `components/spm/pep/violations-panel.tsx`; an itemized
// per-violation feed needs that endpoint to ship first.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
  classOf,
  pepApi,
  type PolicyEligibility,
  type PolicyInstance,
  type PolicySeverity,
  type PolicyTemplate,
} from '@/lib/aispm/pep';

const SEVERITY_CLASSES: Record<PolicySeverity, string> = {
  low: 'bg-gray-100 text-gray-700',
  medium: 'bg-amber-100 text-amber-700',
  high: 'bg-orange-100 text-orange-700',
  critical: 'bg-red-100 text-red-700',
};

function SeverityBadge({ severity }: { severity: PolicySeverity }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${SEVERITY_CLASSES[severity]}`}>
      {severity}
    </span>
  );
}

function StatusBadge({ status }: { status: PolicyInstance['status'] }) {
  return (
    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700">
      {status}
    </span>
  );
}

export default function PolicyInstanceDetailPage() {
  const params = useParams();
  const instanceId = params.id as string;

  const [instance, setInstance] = useState<PolicyInstance | null>(null);
  const [template, setTemplate] = useState<PolicyTemplate | null>(null);
  const [eligibility, setEligibility] = useState<PolicyEligibility | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const inst = await pepApi.getPolicy(instanceId);
      setInstance(inst);
      const tmpl = await pepApi.getTemplate(inst.template_id).catch(() => null);
      setTemplate(tmpl);
      if (classOf(inst.enforcement_mode) === 'guideline') {
        const elig = await pepApi.getEligibility(instanceId).catch(() => null);
        setEligibility(elig);
      } else {
        setEligibility(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load policy instance');
    } finally {
      setLoading(false);
    }
  }, [instanceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleApprove = async (role: string) => {
    setBusy(true);
    setError(null);
    try {
      setEligibility(await pepApi.approve(instanceId, { role }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approval failed');
    } finally {
      setBusy(false);
    }
  };

  const handlePromote = async () => {
    setBusy(true);
    setError(null);
    try {
      setInstance(await pepApi.promote(instanceId));
      setEligibility(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Promotion failed');
    } finally {
      setBusy(false);
    }
  };

  const handleDemote = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await pepApi.demote(instanceId);
      setInstance(updated);
      const elig = await pepApi.getEligibility(instanceId).catch(() => null);
      setEligibility(elig);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Demotion failed');
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <div className="py-12 text-center text-gray-500">Loading policy instance…</div>;
  }

  if (!instance) {
    return (
      <div className="py-8 text-center">
        <p className="text-red-600">{error || 'Policy instance not found'}</p>
        <Link
          href="/dashboard/policy-enforcement"
          className="mt-4 inline-block text-sm text-primary-600 hover:text-primary-800"
        >
          Back to Policy Enforcement
        </Link>
      </div>
    );
  }

  const policyClass = classOf(instance.enforcement_mode);
  const stats = instance.stats;
  const recentEvents = [...instance.promotion_history].sort((a, b) => (a.at < b.at ? 1 : -1));

  return (
    <div>
      <Link href="/dashboard/policy-enforcement" className="text-sm text-gray-500 hover:text-gray-700">
        &larr; Back to Policy Enforcement
      </Link>

      <div className="mt-2 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{instance.name}</h1>
          <p className="mt-1 text-sm text-gray-500">
            {template ? (
              <Link
                href={`/dashboard/policy-enforcement/templates/${template.id}`}
                className="text-primary-600 hover:text-primary-800"
              >
                {template.name}
              </Link>
            ) : (
              instance.template_id
            )}{' '}
            (v{instance.template_version}) · {policyClass === 'strict' ? 'Strict' : 'Guideline'} (
            {instance.enforcement_mode})
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <SeverityBadge severity={instance.severity} />
          <StatusBadge status={instance.status} />
        </div>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {/* Rules — inherited from the template, tuned by this instance's parameter_values */}
          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Rules</h2>
            {template ? (
              <>
                <p className="mt-1 text-xs text-gray-500">
                  Inherited from <span className="font-medium">{template.name}</span> — tuned by this
                  instance&apos;s parameter overrides below.
                </p>
                <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  Detectors
                </h3>
                <ul className="mt-2 space-y-2">
                  {template.detectors.length === 0 && (
                    <li className="text-xs text-gray-500">None defined.</li>
                  )}
                  {template.detectors.map((d, i) => (
                    <li
                      key={i}
                      className="rounded-md bg-gray-50 p-2.5 text-xs text-gray-700 ring-1 ring-gray-100"
                    >
                      <pre className="whitespace-pre-wrap break-words font-mono text-[11px]">
                        {JSON.stringify(d, null, 2)}
                      </pre>
                    </li>
                  ))}
                </ul>
                <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  Actions
                </h3>
                <ul className="mt-2 space-y-2">
                  {template.actions.length === 0 && (
                    <li className="text-xs text-gray-500">None defined.</li>
                  )}
                  {template.actions.map((a, i) => (
                    <li
                      key={i}
                      className="rounded-md bg-gray-50 p-2.5 text-xs text-gray-700 ring-1 ring-gray-100"
                    >
                      <pre className="whitespace-pre-wrap break-words font-mono text-[11px]">
                        {JSON.stringify(a, null, 2)}
                      </pre>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="mt-2 text-xs text-gray-500">
                Source template could not be loaded — showing instance data only.
              </p>
            )}
            {Object.keys(instance.parameter_values).length > 0 && (
              <>
                <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  Parameter overrides
                </h3>
                <pre className="mt-2 whitespace-pre-wrap break-words rounded-md bg-gray-50 p-2.5 font-mono text-[11px] text-gray-700 ring-1 ring-gray-100">
                  {JSON.stringify(instance.parameter_values, null, 2)}
                </pre>
              </>
            )}
          </div>

          {/* Violation history */}
          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Violation history (30d)</h2>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-lg bg-gray-50 p-3 text-center">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Hits</p>
                <p className="mt-1 text-lg font-bold text-gray-900 tabular-nums">
                  {stats.total_hits_30d ?? 0}
                </p>
              </div>
              <div className="rounded-lg bg-red-50 p-3 text-center">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-red-600">Blocked</p>
                <p className="mt-1 text-lg font-bold text-red-700 tabular-nums">
                  {stats.block_count_30d ?? 0}
                </p>
              </div>
              <div className="rounded-lg bg-amber-50 p-3 text-center">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-600">
                  Flagged
                </p>
                <p className="mt-1 text-lg font-bold text-amber-700 tabular-nums">
                  {stats.flag_count_30d ?? 0}
                </p>
              </div>
              <div className="rounded-lg bg-gray-50 p-3 text-center">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                  FP rate
                </p>
                <p className="mt-1 text-lg font-bold text-gray-900 tabular-nums">
                  {stats.false_positive_rate !== undefined
                    ? `${(stats.false_positive_rate * 100).toFixed(1)}%`
                    : '—'}
                </p>
              </div>
            </div>
            <p className="mt-3 text-xs text-gray-500">
              Last triggered:{' '}
              {stats.last_triggered_at ? new Date(stats.last_triggered_at).toLocaleString() : 'never'}
            </p>

            <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-gray-500">
              Promotion / demotion events
            </h3>
            <ul className="mt-2 space-y-1.5">
              {recentEvents.length === 0 && (
                <li className="text-xs text-gray-500">No promotion or demotion events yet.</li>
              )}
              {recentEvents.map((event, i) => (
                <li key={i} className="text-xs text-gray-600">
                  {event.from} → {event.to} by {event.by}
                  {event.reason ? ` — ${event.reason}` : ''}
                  <span className="text-gray-400"> · {new Date(event.at).toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="space-y-4">
          {/* Promote / demote controls */}
          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Lifecycle</h2>

            {policyClass === 'strict' ? (
              <>
                <p className="mt-2 text-xs text-gray-600">
                  This instance is Strict — real-time enforcement. Demote is always permitted (the
                  safety valve).
                </p>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleDemote()}
                  className="mt-3 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Demote to Guideline
                </button>
              </>
            ) : eligibility ? (
              <>
                <p className="mt-2 text-xs text-gray-600">
                  {eligibility.days_in_guideline}/{eligibility.days_required} days in Guideline ·{' '}
                  {(eligibility.false_positive_rate * 100).toFixed(1)}% FP rate (threshold{' '}
                  {(eligibility.fp_rate_threshold * 100).toFixed(1)}%)
                </p>
                <span
                  className={
                    eligibility.eligible
                      ? 'mt-2 inline-flex rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200'
                      : 'mt-2 inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200'
                  }
                >
                  {eligibility.eligible ? 'Eligible' : 'Not eligible'}
                </span>

                {eligibility.approvers.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {eligibility.approvers.map((a) => (
                      <button
                        key={a.role}
                        type="button"
                        disabled={busy || a.status === 'approved'}
                        onClick={() => void handleApprove(a.role)}
                        className={
                          a.status === 'approved'
                            ? 'rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200'
                            : 'rounded-full bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-700 ring-1 ring-inset ring-gray-200 hover:bg-gray-100 disabled:opacity-50'
                        }
                      >
                        {a.status === 'approved' ? `✓ ${a.role}` : `Approve as ${a.role}`}
                      </button>
                    ))}
                  </div>
                )}

                {eligibility.blocking_reasons.length > 0 && (
                  <ul className="mt-3 space-y-0.5 text-[11px] text-amber-700">
                    {eligibility.blocking_reasons.map((r, i) => (
                      <li key={i}>• {r}</li>
                    ))}
                  </ul>
                )}

                <button
                  type="button"
                  disabled={busy || !eligibility.eligible}
                  onClick={() => void handlePromote()}
                  className="mt-3 rounded-md bg-primary-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Promote to Strict
                </button>
              </>
            ) : (
              <p className="mt-2 text-xs text-gray-500">
                {error ? 'Eligibility unavailable.' : 'Loading eligibility…'}
              </p>
            )}
          </div>

          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Applies to</h2>
            <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] text-gray-700">
              {JSON.stringify(instance.applies_to, null, 2)}
            </pre>
          </div>

          {instance.reviewers.length > 0 && (
            <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
              <h2 className="text-sm font-semibold text-gray-900">Reviewers</h2>
              <ul className="mt-2 space-y-1 text-xs text-gray-700">
                {instance.reviewers.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
