'use client';

// ─────────────────────────────────────────────────────────────────────
// Model Risk — per-model risk profile cards (atlas §3.9)
//
// Shows the 6-dimension risk score for each AI model atlas knows
// about (hallucination, bias, toxicity, privacy, security,
// compliance). Lower scores are safer. Each card opens a detail
// drawer with the model's notes and which use cases reference it.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  DEMO_DATA_BANNER,
  MODEL_RISK_PROFILES,
  type ModelRiskProfile,
} from '@/lib/curated-demo-data';
import {
  listModelRiskProfiles,
  type ModelRiskProfile as ApiModelRiskProfile,
} from '@/lib/ai-spm';
import { isDemoFallbackEnabled } from '@/lib/demo-mode';

const DIMENSIONS: Array<{ key: keyof ModelRiskProfile; label: string }> = [
  { key: 'hallucinationScore', label: 'Hallucination' },
  { key: 'biasScore', label: 'Bias' },
  { key: 'toxicityScore', label: 'Toxicity' },
  { key: 'privacyScore', label: 'Privacy' },
  { key: 'securityScore', label: 'Security' },
  { key: 'complianceScore', label: 'Compliance' },
];

function scoreBg(score: number): string {
  if (score >= 25) return 'bg-red-100 text-red-800';
  if (score >= 15) return 'bg-amber-100 text-amber-800';
  return 'bg-emerald-100 text-emerald-800';
}

function profileNotes(profile: ApiModelRiskProfile): string {
  if (profile.key_risks?.length) return profile.key_risks.join(' ');
  if (profile.risk_alerts?.length) {
    return profile.risk_alerts
      .map((alert) =>
        typeof alert.message === 'string' ? alert.message : JSON.stringify(alert),
      )
      .join(' ');
  }
  return 'No risk notes recorded.';
}

function toCardModel(profile: ApiModelRiskProfile): ModelRiskProfile {
  return {
    id: profile.id,
    modelName: profile.name,
    vendor: profile.provider ?? 'Unknown provider',
    hostedOn: profile.category ?? profile.parameters ?? 'Not specified',
    hallucinationScore: profile.hallucination_risk ?? 0,
    biasScore: profile.bias_risk ?? 0,
    toxicityScore: profile.toxicity_risk ?? 0,
    privacyScore: profile.privacy_risk ?? 0,
    securityScore: profile.security_risk ?? 0,
    complianceScore: profile.compliance_risk ?? 0,
    inUseCases: profile.approved_use_cases?.length ?? 0,
    notes: profileNotes(profile),
  };
}

function ModelCard({
  model,
  onOpen,
}: {
  model: ModelRiskProfile;
  onOpen: (m: ModelRiskProfile) => void;
}) {
  return (
    <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">
            {model.modelName}
          </h3>
          <p className="text-xs text-gray-500">
            {model.vendor} · {model.hostedOn}
          </p>
        </div>
        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700">
          {model.inUseCases} use case{model.inUseCases === 1 ? '' : 's'}
        </span>
      </div>

      <ul className="mt-4 grid grid-cols-2 gap-2">
        {DIMENSIONS.map((d) => {
          const score = model[d.key] as number;
          return (
            <li
              key={d.key}
              className={`flex items-center justify-between rounded-md px-2 py-1 text-xs font-medium ${scoreBg(score)}`}
            >
              <span>{d.label}</span>
              <span className="tabular-nums">{score}</span>
            </li>
          );
        })}
      </ul>

      <button
        type="button"
        onClick={() => onOpen(model)}
        className="mt-4 w-full rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
      >
        View notes →
      </button>
    </div>
  );
}

function Drawer({
  model,
  onClose,
}: {
  model: ModelRiskProfile | null;
  onClose: () => void;
}) {
  if (!model) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-gray-900/40"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">
              {model.modelName}
            </h2>
            <p className="mt-0.5 text-xs text-gray-500">
              {model.vendor} · {model.hostedOn}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        <section className="mt-5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Risk dimensions (lower = safer)
          </h3>
          <ul className="mt-3 space-y-2">
            {DIMENSIONS.map((d) => {
              const score = model[d.key] as number;
              return (
                <li
                  key={d.key}
                  className="flex items-center justify-between gap-3"
                >
                  <span className="text-sm text-gray-700">{d.label}</span>
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-24 rounded-full bg-gray-100">
                      <div
                        className={
                          score >= 25
                            ? 'h-2 rounded-full bg-red-500'
                            : score >= 15
                              ? 'h-2 rounded-full bg-amber-500'
                              : 'h-2 rounded-full bg-emerald-500'
                        }
                        style={{ width: `${score}%` }}
                      />
                    </div>
                    <span className="text-xs font-medium text-gray-900 tabular-nums">
                      {score}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>

        <section className="mt-5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Notes
          </h3>
          <p className="mt-1 text-sm text-gray-700">{model.notes}</p>
        </section>

        <section className="mt-5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Used by
          </h3>
          <p className="mt-1 text-sm text-gray-700">
            {model.inUseCases} registered use case
            {model.inUseCases === 1 ? '' : 's'}.{' '}
            <Link
              href={`/dashboard/registry`}
              className="font-medium text-primary-600 hover:text-primary-700"
            >
              Open registry →
            </Link>
          </p>
        </section>
      </div>
    </div>
  );
}

export default function ModelRiskPage() {
  const [open, setOpen] = useState<ModelRiskProfile | null>(null);
  const [profiles, setProfiles] = useState<ApiModelRiskProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void listModelRiskProfiles({ pageSize: 100 })
      .then((response) => {
        if (active) setProfiles(response.profiles);
      })
      .catch((cause: unknown) => {
        if (active) {
          setError(
            cause instanceof Error
              ? cause.message
              : 'Failed to load model risk profiles.',
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

  const isDemo = !loading && profiles.length === 0 && isDemoFallbackEnabled();
  const models = useMemo(
    () => (profiles.length > 0 ? profiles.map(toCardModel) : isDemo ? MODEL_RISK_PROFILES : []),
    [isDemo, profiles],
  );

  const averages = useMemo(() => {
    const total: Record<string, number> = {};
    for (const d of DIMENSIONS) total[d.key] = 0;
    for (const m of models) {
      for (const d of DIMENSIONS) {
        total[d.key] += m[d.key] as number;
      }
    }
    const n = models.length || 1;
    return DIMENSIONS.map((d) => ({
      label: d.label,
      value: Math.round(total[d.key] / n),
    }));
  }, [models]);

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Model risk</h1>
        <p className="mt-1 text-sm text-gray-600">
          Per-model risk profiles across hallucination, bias, toxicity,
          privacy, security, and compliance dimensions.
        </p>
      </div>

      {isDemo && (
        <span className="mt-4 inline-flex rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
          {DEMO_DATA_BANNER}
        </span>
      )}

      {loading ? (
        <div className="py-12 text-center text-gray-500">
          Loading model risk profiles…
        </div>
      ) : models.length === 0 ? (
        <div className="mt-6 rounded-xl bg-white p-8 text-center shadow-sm ring-1 ring-gray-200">
          <p className="text-sm font-medium text-gray-900">
            No model risk profiles yet.
          </p>
          <p className="mt-1 text-sm text-gray-600">
            Profiles will appear here once models are assessed.
          </p>
          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </div>
      ) : (
        <>
      {/* Averages strip */}
      <div className="mt-6 grid grid-cols-3 gap-3 sm:grid-cols-6">
        {averages.map((a) => (
          <div
            key={a.label}
            className="rounded-lg bg-white p-3 text-center shadow-sm ring-1 ring-gray-200"
          >
            <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              {a.label}
            </p>
            <p className="mt-1 text-xl font-bold text-gray-900 tabular-nums">
              {a.value}
            </p>
          </div>
        ))}
      </div>

      {/* Model cards */}
      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        {models.map((m) => (
          <ModelCard key={m.id} model={m} onOpen={setOpen} />
        ))}
      </div>

      <p className="mt-4 text-xs text-gray-500">
        Scores 0–100; lower is safer. Hosted on a Microsoft surface? See{' '}
        <Link
          href="/dashboard/integrations/MICROSOFT_PURVIEW"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Purview classifier
        </Link>{' '}
        events for live data.
      </p>
        </>
      )}

      <Drawer model={open} onClose={() => setOpen(null)} />
    </div>
  );
}
