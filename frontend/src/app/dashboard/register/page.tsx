'use client';

// ─────────────────────────────────────────────────────────────────────
// Register — form-driven Application registration (atlas §3.7)
//
// Promotes a Shadow AI detection to an Active use case, or creates
// one from scratch. Pre-fills `tool` and `title` when the URL carries
// `?app=ChatGPT&detected_users=18` (set by the Discover page's
// Promote link).
//
// On submit: POST /api/v1/use-cases with source=SHADOW_PROMOTE or
// FORM. Soft-fail to local toast when the backend isn't reachable.
// ─────────────────────────────────────────────────────────────────────

import { Suspense, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { DEPARTMENTS, ORG } from '@/lib/curated-demo-data';
import type {
  UseCaseRiskTier,
  UseCaseSource,
  UseCaseStatus,
} from '@/lib/types';

function RegisterForm() {
  const router = useRouter();
  const params = useSearchParams();
  const promotedApp = params.get('app') || '';
  const promotedUsers = params.get('detected_users') || '';

  const [title, setTitle] = useState(
    promotedApp ? `${promotedApp} — pending review` : '',
  );
  const [tool, setTool] = useState(promotedApp);
  const [department, setDepartment] = useState('');
  const [riskTier, setRiskTier] = useState<UseCaseRiskTier>('LOW');
  const [status, setStatus] = useState<UseCaseStatus>('REVIEW');
  const [dataClasses, setDataClasses] = useState('');
  const [frequency, setFrequency] = useState('');
  const [notes, setNotes] = useState(
    promotedUsers
      ? `Promoted from Shadow AI detection (${promotedUsers} users observed in last 30d).`
      : '',
  );
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const source: UseCaseSource = promotedApp ? 'SHADOW_PROMOTE' : 'FORM';

  const handleSubmit = async () => {
    setError(null);
    if (!title.trim()) return setError('Title is required');
    if (!tool.trim()) return setError('Tool is required');
    if (!department) return setError('Department is required');

    setSaving(true);
    try {
      await api.createUseCase({
        title: title.trim(),
        tool: tool.trim(),
        department,
        source,
        status,
        risk_tier: riskTier,
        data_classes: dataClasses
          .split(/[,\n]/)
          .map((s) => s.trim())
          .filter(Boolean),
        frequency: frequency.trim() || null,
        notes: notes.trim() || null,
      });
      setToast(`✓ Registered "${title}"`);
      setTimeout(() => router.push('/dashboard/registry'), 1000);
    } catch {
      setToast(`✓ Registered "${title}" (demo — backend offline)`);
      setTimeout(() => router.push('/dashboard/registry'), 1200);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link
            href={promotedApp ? '/dashboard/discover' : '/dashboard/registry'}
            className="text-xs font-medium text-gray-500 hover:text-gray-700"
          >
            ← {promotedApp ? 'Discover' : 'Registry'}
          </Link>
          <h1 className="mt-1 text-2xl font-bold text-gray-900">
            Register an AI use case
          </h1>
          <p className="mt-1 text-sm text-gray-600">
            {promotedApp
              ? `Promoting a Shadow AI detection (${promotedApp}) to a tracked use case.`
              : `${ORG.name} — capture an AI workflow your team is using or plans to use.`}
          </p>
        </div>
        <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
          Source: {source}
        </span>
      </div>

      <div className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Title
            </span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Grant memo drafting"
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              AI tool
            </span>
            <input
              type="text"
              value={tool}
              onChange={(e) => setTool(e.target.value)}
              placeholder="Microsoft Copilot"
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>

          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Department
            </span>
            <select
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="">— pick one —</option>
              {DEPARTMENTS.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Frequency
            </span>
            <input
              type="text"
              value={frequency}
              onChange={(e) => setFrequency(e.target.value)}
              placeholder="weekly · ad hoc · once per grant cycle"
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>

          <fieldset>
            <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Risk tier
            </legend>
            <div className="mt-1 grid grid-cols-3 gap-2">
              {(['LOW', 'MEDIUM', 'HIGH'] as const).map((tier) => {
                const active = riskTier === tier;
                const colour =
                  tier === 'HIGH'
                    ? active
                      ? 'border-red-500 bg-red-50 text-red-700 ring-1 ring-red-200'
                      : ''
                    : tier === 'MEDIUM'
                      ? active
                        ? 'border-amber-500 bg-amber-50 text-amber-700 ring-1 ring-amber-200'
                        : ''
                      : active
                        ? 'border-emerald-500 bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200'
                        : '';
                return (
                  <button
                    type="button"
                    key={tier}
                    onClick={() => setRiskTier(tier)}
                    className={
                      'rounded-md border px-3 py-1.5 text-xs font-medium ' +
                      (active
                        ? colour
                        : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300')
                    }
                  >
                    {tier}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Initial status
            </span>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as UseCaseStatus)}
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="DRAFT">Draft</option>
              <option value="REVIEW">Under review</option>
              <option value="ACTIVE">Active</option>
            </select>
          </label>

          <label className="col-span-1 block sm:col-span-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Data classes (comma-separated)
            </span>
            <input
              type="text"
              value={dataClasses}
              onChange={(e) => setDataClasses(e.target.value)}
              placeholder="customer_pii, financial_data, strategy_docs"
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>

          <label className="col-span-1 block sm:col-span-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Notes
            </span>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Any context the IT lead should know — owner, workflow, sample prompts."
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>
        </div>

        {error && (
          <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        <div className="mt-6 flex items-center justify-end gap-2 border-t border-gray-100 pt-5">
          <Link
            href="/dashboard/registry"
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </Link>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={saving}
            className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {saving ? 'Registering…' : 'Register'}
          </button>
        </div>
      </div>

      {toast && (
        <div
          className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-xl"
          role="status"
        >
          {toast}
        </div>
      )}
    </div>
  );
}

export default function RegisterPage() {
  // Wrap in Suspense for useSearchParams() — Next 15+ requirement.
  return (
    <Suspense fallback={<div className="text-sm text-gray-500">Loading…</div>}>
      <RegisterForm />
    </Suspense>
  );
}
