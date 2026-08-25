'use client';

// ─────────────────────────────────────────────────────────────────────
// Owner inference — /dashboard/owners/inference (#244)
//
// AISPM's `lib/owner-inference.ts` was a deterministic heuristic over
// the registry + Entra ID / M365 licensing / HR signals. atlas has no
// licensing model, so the backend heuristic (app.services.owner_inference)
// ranks candidates from what actually exists: DirectoryUser (Microsoft
// Graph sync — department, job title, manager chain) for business
// owners, and ORG_ADMIN+/TENANT_ADMIN atlas Users for IT owners.
//
// Accepting a suggestion is a plain PATCH /use-cases/{id} with
// {owner_user_id} — the existing write path (also used by the
// registry page), not a new endpoint.
//
// Soft-fails to a clean empty state if no backend reachable, matching
// the /dashboard/reattestation convention.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { ORG } from '@/lib/curated-demo-data';
import type { OwnerCandidate, OwnerSuggestion } from '@/lib/types';

function confidenceChip(confidence: number): string {
  if (confidence >= 70) return 'bg-emerald-50 text-emerald-700 ring-emerald-200';
  if (confidence >= 40) return 'bg-amber-50 text-amber-700 ring-amber-200';
  return 'bg-gray-100 text-gray-600 ring-gray-200';
}

function CandidateRow({
  candidate,
  onAssign,
  assigning,
}: {
  candidate: OwnerCandidate;
  onAssign: () => void;
  assigning: boolean;
}) {
  return (
    <li className="flex items-start justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium text-gray-900">
            {candidate.display_name}
          </p>
          <span
            className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset tabular-nums ${confidenceChip(candidate.confidence)}`}
          >
            {Math.round(candidate.confidence)}%
          </span>
        </div>
        {candidate.email && (
          <p className="truncate text-xs text-gray-500">{candidate.email}</p>
        )}
        <p className="mt-0.5 text-xs text-gray-500">{candidate.rationale}</p>
      </div>
      <button
        type="button"
        onClick={onAssign}
        disabled={assigning}
        className="shrink-0 rounded-md bg-primary-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300"
      >
        {assigning ? 'Assigning…' : 'Assign'}
      </button>
    </li>
  );
}

function CandidateList({
  title,
  candidates,
  onAssign,
  assigningUserId,
}: {
  title: string;
  candidates: OwnerCandidate[];
  onAssign: (candidate: OwnerCandidate) => void;
  assigningUserId: string | null;
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        {title}
      </p>
      {candidates.length === 0 ? (
        <p className="mt-2 text-xs text-gray-400">No candidates found.</p>
      ) : (
        <ul className="mt-1 divide-y divide-gray-100">
          {candidates.map((c) => (
            <CandidateRow
              key={c.user_id}
              candidate={c}
              onAssign={() => onAssign(c)}
              assigning={assigningUserId === c.user_id}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

export default function OwnerInferencePage() {
  const [suggestions, setSuggestions] = useState<OwnerSuggestion[] | null>(
    null,
  );
  const [assigning, setAssigning] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const res = await api.getOwnerSuggestions({ limit: 100 });
      setSuggestions(res.suggestions);
    } catch {
      setSuggestions([]);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const showToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3000);
  };

  const handleAssign = async (
    useCase: OwnerSuggestion,
    candidate: OwnerCandidate,
  ) => {
    setAssigning(candidate.user_id);
    try {
      await api.updateUseCase(useCase.use_case_id, {
        owner_user_id: candidate.user_id,
      });
      showToast(`✓ ${candidate.display_name} assigned to "${useCase.use_case_title}".`);
      void refresh();
    } catch {
      showToast('Could not assign owner — try again.');
    } finally {
      setAssigning(null);
    }
  };

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Owner inference</h1>
        <p className="mt-1 text-sm text-gray-600">
          {ORG.name} — ranked business + IT owner suggestions for use cases
          with no owner on file, inferred from directory (department,
          seniority) and role signals. Accept a suggestion to assign it.
        </p>
      </div>

      {/* Hero tile */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Unowned use cases
          </p>
          <p className="mt-2 text-3xl font-bold text-gray-900 tabular-nums">
            {suggestions === null ? '—' : suggestions.length}
          </p>
        </div>
      </div>

      {/* Suggestions */}
      <div className="mt-6 space-y-4">
        {suggestions === null && (
          <div className="rounded-xl bg-white p-8 text-center text-sm text-gray-500 shadow-sm ring-1 ring-gray-200">
            Loading…
          </div>
        )}
        {suggestions !== null && suggestions.length === 0 && (
          <div className="rounded-xl bg-white p-10 text-center text-sm text-gray-500 shadow-sm ring-1 ring-gray-200">
            Every registered use case has an owner. Nothing to suggest.
          </div>
        )}
        {(suggestions ?? []).map((s) => (
          <div
            key={s.use_case_id}
            className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-semibold text-gray-900">
                {s.use_case_title}
              </h2>
              <span className="text-xs text-gray-500">{s.department}</span>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-6 sm:grid-cols-2">
              <CandidateList
                title="Business owner candidates"
                candidates={s.business_owner_candidates}
                assigningUserId={assigning}
                onAssign={(c) => handleAssign(s, c)}
              />
              <CandidateList
                title="IT owner candidates"
                candidates={s.it_owner_candidates}
                assigningUserId={assigning}
                onAssign={(c) => handleAssign(s, c)}
              />
            </div>
          </div>
        ))}
      </div>

      <p className="mt-4 text-xs text-gray-500">
        Confidence is a heuristic (department match, seniority signals,
        role) — not a guarantee. Back to{' '}
        <Link
          href="/dashboard/owners"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Owners
        </Link>
        .
      </p>

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
