// ─────────────────────────────────────────────────────────────────────
// PromotionQueue — Guideline instances awaiting Strict promotion, with
// per-instance eligibility (days in Guideline, FP rate, required
// approvers) and Approve / Promote actions that round-trip to
// `/pep/policies/{id}/{approve,promote}`.
// ─────────────────────────────────────────────────────────────────────

import type { PolicyEligibility, PolicyInstance } from '@/lib/aispm/pep';

export interface QueueItem {
  instance: PolicyInstance;
  eligibility: PolicyEligibility | null;
  eligibilityError: string | null;
}

interface PromotionQueueProps {
  items: QueueItem[];
  busyId: string | null;
  onApprove: (instance: PolicyInstance, role: string) => void;
  onPromote: (instance: PolicyInstance) => void;
}

export function PromotionQueue({ items, busyId, onApprove, onPromote }: PromotionQueueProps) {
  return (
    <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
      <h2 className="text-sm font-semibold text-gray-900">Promotion queue</h2>
      <p className="mt-0.5 text-xs text-gray-500">
        Guideline policies gated on time-in-mode, false-positive rate, and required approver sign-off.
      </p>

      {items.length === 0 && (
        <p className="mt-4 text-xs text-gray-500">No policies are in Guideline mode.</p>
      )}

      <ul className="mt-4 space-y-4">
        {items.map(({ instance, eligibility, eligibilityError }) => {
          const busy = busyId === instance.id;
          return (
            <li key={instance.id} className="rounded-lg border border-gray-200 p-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-gray-900">{instance.name}</p>
                {eligibility && (
                  <span
                    className={
                      eligibility.eligible
                        ? 'rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200'
                        : 'rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200'
                    }
                  >
                    {eligibility.eligible ? 'Eligible' : 'Not eligible'}
                  </span>
                )}
              </div>

              {eligibilityError && (
                <p className="mt-2 text-xs text-red-600">{eligibilityError}</p>
              )}

              {eligibility && (
                <>
                  <p className="mt-2 text-xs text-gray-600">
                    {eligibility.days_in_guideline}/{eligibility.days_required} days in Guideline ·{' '}
                    {(eligibility.false_positive_rate * 100).toFixed(1)}% FP rate (threshold{' '}
                    {(eligibility.fp_rate_threshold * 100).toFixed(1)}%)
                  </p>

                  {eligibility.approvers.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {eligibility.approvers.map((a) => (
                        <button
                          key={a.role}
                          type="button"
                          disabled={busy || a.status === 'approved'}
                          onClick={() => onApprove(instance, a.role)}
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
                    <ul className="mt-2 space-y-0.5 text-[11px] text-amber-700">
                      {eligibility.blocking_reasons.map((r, i) => (
                        <li key={i}>• {r}</li>
                      ))}
                    </ul>
                  )}

                  <button
                    type="button"
                    disabled={busy || !eligibility.eligible}
                    onClick={() => onPromote(instance)}
                    className="mt-3 rounded-md bg-primary-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Promote to Strict
                  </button>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
