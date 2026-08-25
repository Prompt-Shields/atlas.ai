'use client';

// ─────────────────────────────────────────────────────────────────────
// Policy Enforcement — /dashboard/policy-enforcement (M2, issue #239)
//
// KPI strip, 30-day violations panel, promotion queue (approve/promote),
// and a two-pane Strict|Loose instance view (with demote). Wired to
// `/api/v1/pep` via `lib/aispm/pep.ts` (backend landed in #237/#238).
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  classOf,
  pepApi,
  type PolicyEligibility,
  type PolicyInstance,
  type PolicyTemplate,
} from '@/lib/aispm/pep';
import { KpiStrip } from '@/components/spm/pep/kpi-strip';
import { ViolationsPanel } from '@/components/spm/pep/violations-panel';
import { PromotionQueue, type QueueItem } from '@/components/spm/pep/promotion-queue';
import { PolicyPane } from '@/components/spm/pep/policy-pane';

export default function PolicyEnforcementPage() {
  const [templates, setTemplates] = useState<PolicyTemplate[]>([]);
  const [policies, setPolicies] = useState<PolicyInstance[]>([]);
  const [eligibilityById, setEligibilityById] = useState<Record<string, PolicyEligibility>>({});
  const [eligibilityErrorById, setEligibilityErrorById] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadEligibility = useCallback(async (instances: PolicyInstance[]) => {
    const guideline = instances.filter((i) => classOf(i.enforcement_mode) === 'guideline');
    const results = await Promise.all(
      guideline.map(async (i) => {
        try {
          const eligibility = await pepApi.getEligibility(i.id);
          return { id: i.id, eligibility, error: null as string | null };
        } catch (e) {
          return {
            id: i.id,
            eligibility: null,
            error: e instanceof Error ? e.message : 'Failed to load eligibility',
          };
        }
      }),
    );
    const nextEligibility: Record<string, PolicyEligibility> = {};
    const nextErrors: Record<string, string> = {};
    for (const r of results) {
      if (r.eligibility) nextEligibility[r.id] = r.eligibility;
      if (r.error) nextErrors[r.id] = r.error;
    }
    setEligibilityById(nextEligibility);
    setEligibilityErrorById(nextErrors);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [templateList, policyList] = await Promise.all([
        pepApi.listTemplates(),
        pepApi.listPolicies(),
      ]);
      setTemplates(templateList);
      setPolicies(policyList);
      await loadEligibility(policyList);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load policy enforcement data');
    } finally {
      setLoading(false);
    }
  }, [loadEligibility]);

  useEffect(() => {
    void load();
  }, [load]);

  const templatesById = useMemo(() => new Map(templates.map((t) => [t.id, t])), [templates]);

  const strict = useMemo(
    () => policies.filter((p) => classOf(p.enforcement_mode) === 'strict'),
    [policies],
  );
  const loose = useMemo(
    () => policies.filter((p) => classOf(p.enforcement_mode) === 'guideline'),
    [policies],
  );

  const kpis = useMemo(() => {
    const coveragePct =
      templates.length > 0
        ? (new Set(policies.map((p) => p.template_id)).size / templates.length) * 100
        : 0;
    const blocks30d = policies.reduce((sum, p) => sum + (p.stats.block_count_30d ?? 0), 0);
    const promotionsReady = loose.filter((p) => eligibilityById[p.id]?.eligible).length;
    return { strict: strict.length, loose: loose.length, coveragePct, blocks30d, promotionsReady };
  }, [templates.length, policies, strict.length, loose, eligibilityById]);

  const queueItems: QueueItem[] = useMemo(
    () =>
      loose.map((instance) => ({
        instance,
        eligibility: eligibilityById[instance.id] ?? null,
        eligibilityError: eligibilityErrorById[instance.id] ?? null,
      })),
    [loose, eligibilityById, eligibilityErrorById],
  );

  const handleApprove = async (instance: PolicyInstance, role: string) => {
    setBusyId(instance.id);
    setError(null);
    try {
      const eligibility = await pepApi.approve(instance.id, { role });
      setEligibilityById((prev) => ({ ...prev, [instance.id]: eligibility }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approval failed');
    } finally {
      setBusyId(null);
    }
  };

  const handlePromote = async (instance: PolicyInstance) => {
    setBusyId(instance.id);
    setError(null);
    try {
      const updated = await pepApi.promote(instance.id);
      setPolicies((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      setEligibilityById((prev) => {
        const next = { ...prev };
        delete next[updated.id];
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Promotion failed');
    } finally {
      setBusyId(null);
    }
  };

  const handleDemote = async (instance: PolicyInstance) => {
    setBusyId(instance.id);
    setError(null);
    try {
      const updated = await pepApi.demote(instance.id);
      setPolicies((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      await loadEligibility([...policies.filter((p) => p.id !== updated.id), updated]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Demotion failed');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Policy Enforcement</h1>
        <p className="mt-1 text-sm text-gray-600">
          LLM prompt policy lifecycle — Guideline (observe) to Strict (block/redact), gated by
          time-in-mode, false-positive rate, and approver sign-off.
        </p>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
          {error}
        </p>
      )}

      {loading ? (
        <div className="mt-8 py-12 text-center text-gray-500">Loading policy enforcement data…</div>
      ) : (
        <div className="mt-6 space-y-6">
          <KpiStrip {...kpis} />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <ViolationsPanel policies={policies} />
            <PromotionQueue
              items={queueItems}
              busyId={busyId}
              onApprove={(instance, role) => void handleApprove(instance, role)}
              onPromote={(instance) => void handlePromote(instance)}
            />
          </div>

          <PolicyPane
            strict={strict}
            loose={loose}
            templatesById={templatesById}
            busyId={busyId}
            onDemote={(instance) => void handleDemote(instance)}
          />
        </div>
      )}
    </div>
  );
}
