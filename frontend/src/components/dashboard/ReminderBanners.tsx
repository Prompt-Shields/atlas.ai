'use client';

// ─────────────────────────────────────────────────────────────────────
// ReminderBanners — dashboard-wide nudges for Øystein's three priorities.
//
// Closes the operational loop on PRs #52 / #54. Without these
// banners, the handbook just sits in nav and re-attestation reviews
// only surface for admins who navigate into /dashboard/reattestation.
// These banners put the right action in front of the right user
// on every dashboard page until they act.
//
// Two banner types, both dismissible-for-session (sessionStorage)
// but not permanently — once acknowledged on the actual feature
// page, the banner disappears via the backend state.
//
// 1. HandbookBanner       priority #1 — read the handbook
// 2. OverdueReviewsBanner priority #3 — owner of N overdue reviews
//
// Soft-fails to no-render if backend isn't reachable. Cheap two-
// request fetch on mount; never blocks dashboard render.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type {
  HandbookStatusResponse,
  UseCaseReviewListResponse,
} from '@/lib/types';

const HANDBOOK_DISMISS_KEY = 'atlas.banner.handbook.dismissed';
const REVIEWS_DISMISS_KEY = 'atlas.banner.reviews.dismissed';

function isDismissedThisSession(key: string): boolean {
  if (typeof window === 'undefined') return false;
  return sessionStorage.getItem(key) === '1';
}

function dismissForSession(key: string): void {
  if (typeof window === 'undefined') return;
  sessionStorage.setItem(key, '1');
}


// ─── Handbook banner ─────────────────────────────────────────────────


function HandbookBanner({
  status,
  onDismiss,
}: {
  status: HandbookStatusResponse;
  onDismiss: () => void;
}) {
  if (status.acknowledged) return null;
  return (
    <div
      className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border-l-4 border-amber-400 bg-amber-50 px-4 py-3"
      role="region"
      aria-label="AI governance handbook reminder"
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-amber-900">
          Please read the AI governance handbook (5 min)
        </p>
        <p className="mt-0.5 text-xs text-amber-800">
          New here? Atlas asks every user to read the handbook once.
          It explains what data atlas sees (SHA-256 hashes only —
          never your prompts) and what&apos;s expected of you. Current
          version: <strong>v{status.current_version}</strong>.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-md px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100"
        >
          Not now
        </button>
        <Link
          href="/dashboard/handbook"
          className="rounded-md bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700"
        >
          Read the handbook →
        </Link>
      </div>
    </div>
  );
}


// ─── Overdue reviews banner ──────────────────────────────────────────


function OverdueReviewsBanner({
  reviewsOwned,
  onDismiss,
}: {
  reviewsOwned: UseCaseReviewListResponse;
  onDismiss: () => void;
}) {
  const count = reviewsOwned.total;
  if (count === 0) return null;
  return (
    <div
      className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border-l-4 border-red-400 bg-red-50 px-4 py-3"
      role="region"
      aria-label="Overdue use-case re-attestation reminder"
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-red-900">
          {count} use case{count === 1 ? '' : 's'} you own{' '}
          {count === 1 ? 'is' : 'are'} overdue for re-attestation
        </p>
        <p className="mt-0.5 text-xs text-red-800">
          A quick 90-second check-in confirms the registered fields
          (tool, model, data classes, user base) still match reality.
          Flag drift if anything material has changed.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-md px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
        >
          Snooze
        </button>
        <Link
          href="/dashboard/reattestation?status=OVERDUE"
          className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700"
        >
          Review {count === 1 ? 'it' : 'them'} now →
        </Link>
      </div>
    </div>
  );
}


// ─── Wrapper ─────────────────────────────────────────────────────────


export function ReminderBanners() {
  const [handbookStatus, setHandbookStatus] =
    useState<HandbookStatusResponse | null>(null);
  const [overdueOwned, setOverdueOwned] =
    useState<UseCaseReviewListResponse | null>(null);
  const [handbookDismissed, setHandbookDismissed] = useState(false);
  const [reviewsDismissed, setReviewsDismissed] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    setHandbookDismissed(isDismissedThisSession(HANDBOOK_DISMISS_KEY));
    setReviewsDismissed(isDismissedThisSession(REVIEWS_DISMISS_KEY));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await api.getHandbookStatus();
        if (!cancelled) setHandbookStatus(s);
      } catch {
        /* soft fail */
      }
      try {
        const r = await api.listUseCaseReviews({
          status: 'OVERDUE',
          mine: true,
          page_size: 1,
        });
        if (!cancelled) setOverdueOwned(r);
      } catch {
        /* soft fail */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const showHandbook =
    !handbookDismissed && handbookStatus && !handbookStatus.acknowledged;
  const showReviews =
    !reviewsDismissed && overdueOwned && overdueOwned.total > 0;

  if (!showHandbook && !showReviews) return null;

  return (
    <div className="mb-6">
      {showHandbook && handbookStatus && (
        <HandbookBanner
          status={handbookStatus}
          onDismiss={() => {
            dismissForSession(HANDBOOK_DISMISS_KEY);
            setHandbookDismissed(true);
          }}
        />
      )}
      {showReviews && overdueOwned && (
        <OverdueReviewsBanner
          reviewsOwned={overdueOwned}
          onDismiss={() => {
            dismissForSession(REVIEWS_DISMISS_KEY);
            setReviewsDismissed(true);
          }}
        />
      )}
    </div>
  );
}
