'use client';

// ─────────────────────────────────────────────────────────────────────
// Policy template catalog — /dashboard/policy-enforcement/templates
// (M2, issue #240)
//
// Lists the shared, read-only PEP template catalog. "Use template"
// clones the template into a new tenant instance (Guideline mode) and
// jumps to the new instance's detail page.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { pepApi, type PolicyCategory, type PolicySeverity, type PolicyTemplate } from '@/lib/aispm/pep';

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

const CATEGORY_LABELS: Record<PolicyCategory, string> = {
  OWASP_LLM: 'OWASP LLM',
  EU_AI_ACT: 'EU AI Act',
  GDPR: 'GDPR',
  INDUSTRY: 'Industry',
  SHADOW_AI: 'Shadow AI',
  CONTENT_SAFETY: 'Content Safety',
  CUSTOM: 'Custom',
};

export default function PolicyTemplateCatalogPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<PolicyTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cloningId, setCloningId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTemplates(await pepApi.listTemplates());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load policy templates');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleUseTemplate = async (template: PolicyTemplate) => {
    setCloningId(template.id);
    setError(null);
    try {
      const instance = await pepApi.cloneTemplate(template.id);
      router.push(`/dashboard/policy-enforcement/policies/${instance.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to clone template');
      setCloningId(null);
    }
  };

  return (
    <div>
      <div>
        <Link
          href="/dashboard/policy-enforcement"
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          &larr; Back to Policy Enforcement
        </Link>
        <h1 className="mt-2 text-2xl font-bold text-gray-900">Policy Template Catalog</h1>
        <p className="mt-1 text-sm text-gray-600">
          Shared, read-only PEP templates — clone one into a tenant instance to start observing
          (Guideline mode) before promoting to Strict enforcement.
        </p>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-8 py-12 text-center text-gray-500">Loading templates…</div>
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {templates.length === 0 && (
            <p className="col-span-full text-sm text-gray-500">No templates in the catalog.</p>
          )}
          {templates.map((template) => (
            <div
              key={template.id}
              className="flex flex-col rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200"
            >
              <div className="flex items-start justify-between gap-2">
                <Link
                  href={`/dashboard/policy-enforcement/templates/${template.id}`}
                  className="text-sm font-semibold text-gray-900 hover:text-primary-700"
                >
                  {template.name}
                </Link>
                <SeverityBadge severity={template.severity} />
              </div>
              <p className="mt-1 text-xs text-gray-500">
                {CATEGORY_LABELS[template.category]} · v{template.version}
              </p>
              <p className="mt-3 flex-1 text-xs text-gray-600 line-clamp-3">{template.description}</p>
              <div className="mt-4 flex items-center justify-between">
                <Link
                  href={`/dashboard/policy-enforcement/templates/${template.id}`}
                  className="text-xs font-medium text-primary-600 hover:text-primary-800"
                >
                  View details
                </Link>
                <button
                  type="button"
                  disabled={cloningId === template.id}
                  onClick={() => void handleUseTemplate(template)}
                  className="rounded-md bg-primary-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {cloningId === template.id ? 'Cloning…' : 'Use template'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
