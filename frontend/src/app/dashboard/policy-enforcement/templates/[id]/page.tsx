'use client';

// ─────────────────────────────────────────────────────────────────────
// Policy template detail — /dashboard/policy-enforcement/templates/[id]
// (M2, issue #240)
//
// Full template definition (triggers, detectors, actions, tunable
// parameters) plus a "Use template" clone action.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { pepApi, type PolicySeverity, type PolicyTemplate } from '@/lib/aispm/pep';

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

function JsonList({ items }: { items: Array<Record<string, unknown>> }) {
  if (items.length === 0) {
    return <p className="text-xs text-gray-500">None defined.</p>;
  }
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="rounded-md bg-gray-50 p-2.5 text-xs text-gray-700 ring-1 ring-gray-100">
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px]">
            {JSON.stringify(item, null, 2)}
          </pre>
        </li>
      ))}
    </ul>
  );
}

export default function PolicyTemplateDetailPage() {
  const params = useParams();
  const router = useRouter();
  const templateId = params.id as string;

  const [template, setTemplate] = useState<PolicyTemplate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cloning, setCloning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTemplate(await pepApi.getTemplate(templateId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load template');
    } finally {
      setLoading(false);
    }
  }, [templateId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleUseTemplate = async () => {
    if (!template) return;
    setCloning(true);
    setError(null);
    try {
      const instance = await pepApi.cloneTemplate(template.id);
      router.push(`/dashboard/policy-enforcement/policies/${instance.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to clone template');
      setCloning(false);
    }
  };

  if (loading) {
    return <div className="py-12 text-center text-gray-500">Loading template…</div>;
  }

  if (!template) {
    return (
      <div className="py-8 text-center">
        <p className="text-red-600">{error || 'Template not found'}</p>
        <Link
          href="/dashboard/policy-enforcement/templates"
          className="mt-4 inline-block text-sm text-primary-600 hover:text-primary-800"
        >
          Back to Template Catalog
        </Link>
      </div>
    );
  }

  return (
    <div>
      <Link
        href="/dashboard/policy-enforcement/templates"
        className="text-sm text-gray-500 hover:text-gray-700"
      >
        &larr; Back to Template Catalog
      </Link>

      <div className="mt-2 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{template.name}</h1>
          <p className="mt-1 text-sm text-gray-500">
            {template.category} · v{template.version} · by {template.author}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <SeverityBadge severity={template.severity} />
          <button
            type="button"
            disabled={cloning}
            onClick={() => void handleUseTemplate()}
            className="rounded-md bg-primary-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {cloning ? 'Cloning…' : 'Use template'}
          </button>
        </div>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Description</h2>
            <p className="mt-2 text-sm text-gray-700">{template.description}</p>
            <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-gray-500">
              Rationale
            </h3>
            <p className="mt-1 text-sm text-gray-700">{template.rationale}</p>
          </div>

          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Examples</h2>
            <h3 className="mt-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
              Violation
            </h3>
            <p className="mt-1 rounded-md bg-red-50 p-2.5 text-xs text-red-700 ring-1 ring-red-100">
              {template.example_violation}
            </p>
            {template.example_safe_input && (
              <>
                <h3 className="mt-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  Safe input
                </h3>
                <p className="mt-1 rounded-md bg-emerald-50 p-2.5 text-xs text-emerald-700 ring-1 ring-emerald-100">
                  {template.example_safe_input}
                </p>
              </>
            )}
          </div>

          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Triggers</h2>
            <div className="mt-2">
              <JsonList items={template.triggers} />
            </div>
          </div>

          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Detectors</h2>
            <div className="mt-2">
              <JsonList items={template.detectors} />
            </div>
          </div>

          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Actions</h2>
            <div className="mt-2">
              <JsonList items={template.actions} />
            </div>
          </div>

          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Tunable parameters</h2>
            <div className="mt-2">
              <JsonList items={template.tunable_parameters} />
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
            <h2 className="text-sm font-semibold text-gray-900">Reference</h2>
            <dl className="mt-2 space-y-2 text-xs">
              <div>
                <dt className="font-medium text-gray-500">OWASP reference</dt>
                <dd className="mt-0.5 text-gray-800">{template.owasp_reference ?? '—'}</dd>
              </div>
              <div>
                <dt className="font-medium text-gray-500">Regulatory references</dt>
                <dd className="mt-0.5 text-gray-800">
                  {template.regulatory_references.length > 0
                    ? template.regulatory_references.join(', ')
                    : '—'}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-gray-500">Default enforcement mode</dt>
                <dd className="mt-0.5 text-gray-800">{template.default_enforcement_mode}</dd>
              </div>
            </dl>
          </div>

          {template.tags.length > 0 && (
            <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
              <h2 className="text-sm font-semibold text-gray-900">Tags</h2>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {template.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
