'use client';

// ─────────────────────────────────────────────────────────────────────
// Re-attestation — /dashboard/reattestation (Øystein priority #3)
//
// "Holde informasjonen oppdatert" — keep information up to date.
//
// Surfaces the UseCaseReview backlog: which registered use cases
// are due (or overdue) for owner re-attestation, plus the drift
// signal that lets the IT lead see which ones have changed since
// last review (model swap, new data classes, broader user base).
//
// Backend: /api/v1/use-case-reviews — populated by the
// `reattestation_enqueue` worker that runs daily and creates Review
// rows for use cases stale beyond the 90-day cadence.
//
// Soft-fails to a clean empty state if no backend reachable; the
// curated demo data doesn't include reviews yet (added separately
// when the demo seed grows).
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { ORG } from '@/lib/curated-demo-data';
import { parseDriftFindings, type ParsedDriftFinding } from '@/lib/drift-notes';
import type {
  ReviewStatusServer,
  UseCaseReviewCounts,
  UseCaseReviewResponse,
} from '@/lib/types';


const STATUS_CHIP: Record<ReviewStatusServer, string> = {
  DUE: 'bg-amber-50 text-amber-700 ring-amber-200',
  REVIEWED: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  OVERDUE: 'bg-red-50 text-red-700 ring-red-200',
  DISMISSED: 'bg-gray-100 text-gray-700 ring-gray-200',
};

const STATUS_LABEL: Record<ReviewStatusServer, string> = {
  DUE: 'Due',
  REVIEWED: 'Reviewed',
  OVERDUE: 'Overdue',
  DISMISSED: 'Dismissed',
};


function StatusChip({ status }: { status: ReviewStatusServer }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_CHIP[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}


// ─── Review modal — captures notes + drift flag ──────────────────────


function ReviewModal({
  review,
  onClose,
  onSubmitted,
}: {
  review: UseCaseReviewResponse | null;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const [notes, setNotes] = useState('');
  const [drift, setDrift] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setNotes('');
    setDrift(false);
    setError(null);
  }, [review?.id]);

  if (!review) return null;

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.markUseCaseReviewed(review.id, {
        notes: notes.trim() || undefined,
        drift_observed: drift,
      });
      onSubmitted();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Could not save — try again.',
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl ring-1 ring-gray-200">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900">
              Confirm: {review.use_case_title}
            </h2>
            <p className="mt-1 text-xs text-gray-600">
              {review.use_case_tool} · {review.use_case_department}
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

        <p className="mt-4 text-sm text-gray-700">
          Is this use case still accurate? Owner check-ins happen every
          90 days — confirm the model, tool, data classes, and user
          base haven&apos;t materially changed.
        </p>

        <label className="mt-4 flex items-start gap-2 text-sm text-gray-800">
          <input
            type="checkbox"
            checked={drift}
            onChange={(e) => setDrift(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
          />
          <span>
            <strong>Drift observed</strong> — something material changed
            since the last review (model swap, new data class, broader
            user base). The IT lead will see this and drill in.
          </span>
        </label>

        <label className="mt-4 block">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Notes (optional)
          </span>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="e.g. switched from GPT-3.5 to GPT-4o in April; same data classes"
            className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          />
        </label>

        {error && (
          <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
            {error}
          </p>
        )}

        <div className="mt-5 flex items-center justify-end gap-2 border-t border-gray-100 pt-4">
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
            disabled={saving}
            className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {saving ? 'Saving…' : 'Mark reviewed'}
          </button>
        </div>
      </div>
    </div>
  );
}


// ─── Page ─────────────────────────────────────────────────────────────


export default function ReattestationPage() {
  const [reviews, setReviews] = useState<UseCaseReviewResponse[] | null>(
    null,
  );
  const [counts, setCounts] = useState<UseCaseReviewCounts | null>(null);
  const [statusFilter, setStatusFilter] = useState<
    ReviewStatusServer | 'all'
  >('all');
  const [selected, setSelected] = useState<UseCaseReviewResponse | null>(
    null,
  );
  const [toast, setToast] = useState<string | null>(null);
  // Expanded review IDs — when drift is flagged, clicking the badge
  // toggles a details panel with the parsed findings.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleExpand = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const refresh = async () => {
    try {
      const res = await api.listUseCaseReviews({
        status: statusFilter === 'all' ? undefined : statusFilter,
        page_size: 100,
      });
      setReviews(res.reviews);
    } catch {
      setReviews([]);
    }
    try {
      const c = await api.getUseCaseReviewCounts();
      setCounts(c);
    } catch {
      setCounts({
        due: 0,
        overdue: 0,
        reviewed: 0,
        dismissed: 0,
        drift_observed_count: 0,
      });
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  const handleReviewed = () => {
    setToast('✓ Marked reviewed — thanks for keeping the registry fresh.');
    setSelected(null);
    void refresh();
    window.setTimeout(() => setToast(null), 3000);
  };

  const handleDismiss = async (review: UseCaseReviewResponse) => {
    const reason = window.prompt(
      `Dismiss the review for "${review.use_case_title}"?\n\nReason (will be logged):`,
    );
    if (!reason || !reason.trim()) return;
    try {
      await api.dismissUseCaseReview(review.id, reason.trim());
      setToast('Review dismissed.');
      void refresh();
      window.setTimeout(() => setToast(null), 3000);
    } catch {
      setToast('Dismiss failed — try again.');
      window.setTimeout(() => setToast(null), 3000);
    }
  };

  const handleEnqueue = async () => {
    try {
      const res = await fetch('/api/v1/use-case-reviews/enqueue', {
        method: 'POST',
      });
      if (!res.ok) throw new Error('enqueue failed');
      setToast('✓ Enqueue triggered. Refreshing…');
      void refresh();
    } catch {
      setToast('Enqueue request failed (admin-only endpoint).');
    } finally {
      window.setTimeout(() => setToast(null), 3000);
    }
  };

  const totals = useMemo(
    () =>
      counts ?? {
        due: 0,
        overdue: 0,
        reviewed: 0,
        dismissed: 0,
        drift_observed_count: 0,
      },
    [counts],
  );

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Re-attestation
          </h1>
          <p className="mt-1 text-sm text-gray-600">
            {ORG.name} — owner check-ins on registered AI use cases.
            Every 90 days, atlas asks the use-case owner: <em>is this
            still accurate?</em> Drift (model swap, new data classes,
            broader user base) gets surfaced here for the IT lead.
          </p>
        </div>
        <button
          type="button"
          onClick={handleEnqueue}
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          title="OrgAdmin-only — trigger the enqueue job on demand"
        >
          Run enqueue now
        </button>
      </div>

      {/* Hero tiles */}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-5">
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Due
          </p>
          <p className="mt-2 text-3xl font-bold text-amber-600 tabular-nums">
            {totals.due}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Overdue
          </p>
          <p className="mt-2 text-3xl font-bold text-red-600 tabular-nums">
            {totals.overdue}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Reviewed
          </p>
          <p className="mt-2 text-3xl font-bold text-emerald-600 tabular-nums">
            {totals.reviewed}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Dismissed
          </p>
          <p className="mt-2 text-3xl font-bold text-gray-700 tabular-nums">
            {totals.dismissed}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Drift flagged
          </p>
          <p className="mt-2 text-3xl font-bold text-amber-700 tabular-nums">
            {totals.drift_observed_count}
          </p>
        </div>
      </div>

      {/* Status filter */}
      <div className="mt-6 inline-flex rounded-lg border border-gray-200 bg-white p-0.5 shadow-sm">
        {(['all', 'DUE', 'OVERDUE', 'REVIEWED', 'DISMISSED'] as const).map(
          (s) => {
            const active = statusFilter === s;
            return (
              <button
                key={s}
                type="button"
                onClick={() => setStatusFilter(s)}
                className={
                  active
                    ? 'rounded-md bg-primary-600 px-3 py-1 text-xs font-semibold text-white'
                    : 'rounded-md px-3 py-1 text-xs font-medium text-gray-600 hover:text-gray-900'
                }
              >
                {s === 'all' ? 'All' : STATUS_LABEL[s]}
              </button>
            );
          },
        )}
      </div>

      {/* Review table */}
      <div className="mt-6 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-100 text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-4 py-2">Use case</th>
                <th className="px-4 py-2">Tool</th>
                <th className="px-4 py-2">Department</th>
                <th className="px-4 py-2">Due</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Drift</th>
                <th className="px-4 py-2 text-right"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {reviews === null && (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    Loading…
                  </td>
                </tr>
              )}
              {reviews !== null && reviews.length === 0 && (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-10 text-center text-sm text-gray-500"
                  >
                    No reviews due. The 90-day enqueue runs daily —
                    use cases will appear here as their cycle comes
                    around.
                  </td>
                </tr>
              )}
              {(reviews ?? []).map((r) => {
                const findings = r.drift_observed ? parseDriftFindings(r.notes) : [];
                const hasFindings = findings.length > 0;
                const isExpanded = expanded.has(r.id);
                return (
                  <FragmentRow key={r.id}>
                    <tr className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-900">
                        {r.use_case_title}
                      </td>
                      <td className="px-4 py-3 text-gray-700">{r.use_case_tool}</td>
                      <td className="px-4 py-3 text-gray-700">
                        {r.use_case_department}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                        {new Date(r.due_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3">
                        <StatusChip status={r.status} />
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-700">
                        {r.drift_observed ? (
                          hasFindings ? (
                            <button
                              type="button"
                              onClick={() => toggleExpand(r.id)}
                              className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200 hover:bg-amber-100"
                              aria-expanded={isExpanded}
                            >
                              Drift flagged · {findings.length}{' '}
                              {isExpanded ? '▾' : '▸'}
                            </button>
                          ) : (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200">
                              Drift flagged
                            </span>
                          )
                        ) : (
                          <span className="text-gray-400">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {(r.status === 'DUE' || r.status === 'OVERDUE') && (
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => handleDismiss(r)}
                              className="text-xs font-medium text-gray-500 hover:text-gray-700"
                            >
                              Dismiss
                            </button>
                            <button
                              type="button"
                              onClick={() => setSelected(r)}
                              className="rounded-md bg-primary-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-primary-700"
                            >
                              Mark reviewed
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                    {isExpanded && hasFindings && (
                      <tr className="bg-amber-50/40">
                        <td colSpan={7} className="px-4 py-3">
                          <DriftFindingsList findings={findings} />
                        </td>
                      </tr>
                    )}
                  </FragmentRow>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <p className="mt-4 text-xs text-gray-500">
        Cadence: 90 days from last review (or registration). Reviews
        gain a 14-day grace period before flipping to{' '}
        <strong>Overdue</strong>. The{' '}
        <Link
          href="/dashboard/registry"
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          Registry
        </Link>{' '}
        page links here for any owner-facing prompts.
      </p>

      <ReviewModal
        review={selected}
        onClose={() => setSelected(null)}
        onSubmitted={handleReviewed}
      />
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


// ─── Helpers: drift-finding panel + row fragment ─────────────────────


/** React doesn't accept a fragment with a key prop directly in some
 *  tooling — wrapping the (data-row, optional details-row) pair lets
 *  us key by review id without TS complaining. */
function FragmentRow({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}


/** Renders the parsed [atlas-drift] findings expanded under a review
 *  row. Each finding shows severity colour, kind label, title, and
 *  the long-form detail copy the rule engine produced. */
function DriftFindingsList({ findings }: { findings: ParsedDriftFinding[] }) {
  return (
    <ul className="space-y-2">
      {findings.map((f, i) => (
        <li
          key={f.signature || i}
          className="rounded-md bg-white p-3 ring-1 ring-amber-200"
        >
          <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-wide">
            <span
              className={`rounded-full px-2 py-0.5 font-semibold ${
                f.severity === 'HIGH'
                  ? 'bg-red-100 text-red-800'
                  : f.severity === 'MEDIUM'
                  ? 'bg-amber-100 text-amber-800'
                  : 'bg-slate-100 text-slate-700'
              }`}
            >
              {f.severity}
            </span>
            <span className="text-slate-500">{driftKindLabel(f.kind)}</span>
          </div>
          <p className="mt-1 text-sm font-medium text-slate-900">{f.title}</p>
          <p className="mt-0.5 text-xs leading-relaxed text-slate-600">
            {f.detail}
          </p>
        </li>
      ))}
    </ul>
  );
}


function driftKindLabel(kind: string): string {
  switch (kind) {
    case 'MODEL_DEPRECATION':
      return 'Model deprecation';
    case 'TOOL_REBRAND':
      return 'Tool rebrand';
    case 'RISK_TIER_MISMATCH':
      return 'Risk tier mismatch';
    default:
      return kind;
  }
}
