// ─────────────────────────────────────────────────────────────────────
// LaunchCampaignModal — focused modal for the Discover page's
// "AI Discovery Campaign" entry card. Collects an audience (all /
// departments / custom) + a delivery channel (Slack / Email) and
// dispatches the REAL `foundation-use-case` survey via the survey
// engine (`POST /surveys/dispatches`, OrgAdmin-gated).
//
// Intentionally self-contained: it does NOT reuse the Registry page's
// Send-survey modal. The Registry modal carries template/tools
// machinery this entry point doesn't need.
// ─────────────────────────────────────────────────────────────────────
'use client';

import { useEffect, useRef, useState } from 'react';
import { DEPARTMENTS } from '@/lib/curated-demo-data';
import type { AudienceFilter } from '@/lib/types';

export type CampaignChannel = 'slack' | 'email';

export interface CampaignDispatch {
  audience: AudienceFilter;
  channel: CampaignChannel;
}

type AudienceMode = 'all' | 'departments' | 'custom';

export function LaunchCampaignModal({
  open,
  onClose,
  onDispatch,
}: {
  open: boolean;
  onClose: () => void;
  onDispatch: (d: CampaignDispatch) => void;
}) {
  const [mode, setMode] = useState<AudienceMode>('all');
  const [selectedDepts, setSelectedDepts] = useState<string[]>([]);
  const [customList, setCustomList] = useState('');
  const [channel, setChannel] = useState<CampaignChannel>('slack');
  const [error, setError] = useState<string | null>(null);
  const firstFieldRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setMode('all');
      setSelectedDepts([]);
      setCustomList('');
      setChannel('slack');
      setError(null);
      const id = window.setTimeout(() => firstFieldRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key === 'Tab') {
        const panel = panelRef.current;
        if (!panel) return;
        const focusable = panel.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (e.shiftKey) {
          if (active === first || !panel.contains(active)) {
            e.preventDefault();
            last.focus();
          }
        } else if (active === last || !panel.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const parsedEmails = customList
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);

  const toggleDept = (name: string) => {
    setSelectedDepts((arr) =>
      arr.includes(name) ? arr.filter((x) => x !== name) : [...arr, name],
    );
  };

  const submit = () => {
    if (mode === 'departments' && selectedDepts.length === 0) {
      setError('Pick at least one department');
      return;
    }
    if (mode === 'custom' && parsedEmails.length === 0) {
      setError('Paste at least one email address');
      return;
    }
    const values =
      mode === 'departments'
        ? selectedDepts
        : mode === 'custom'
          ? parsedEmails
          : [];
    onDispatch({ audience: { mode, values }, channel });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="launch-campaign-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="w-full max-w-xl rounded-xl bg-white p-6 shadow-xl ring-1 ring-gray-200"
      >
        <div className="flex items-center justify-between">
          <h2
            id="launch-campaign-title"
            className="text-lg font-semibold text-gray-900"
          >
            Launch AI Discovery Campaign
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        <p className="mt-1 text-xs text-gray-500">
          Dispatches the foundation use-case survey. Agents interview each
          recipient about AI tool usage, data handling, and risk exposure.
        </p>

        <div className="mt-4 space-y-4">
          {/* Audience */}
          <fieldset>
            <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Audience
            </legend>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {(
                [
                  ['all', 'All staff'],
                  ['departments', 'By department'],
                  ['custom', 'Custom list'],
                ] as const
              ).map(([key, label], i) => {
                const active = mode === key;
                return (
                  <button
                    type="button"
                    key={key}
                    ref={i === 0 ? firstFieldRef : undefined}
                    onClick={() => {
                      setMode(key);
                      setError(null);
                    }}
                    className={
                      active
                        ? 'rounded-md border border-primary-500 bg-primary-50 px-3 py-2 text-xs font-semibold text-primary-900 ring-1 ring-primary-200'
                        : 'rounded-md border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 hover:border-gray-300'
                    }
                  >
                    {label}
                  </button>
                );
              })}
            </div>

            {mode === 'departments' && (
              <div className="mt-3 max-h-44 overflow-y-auto rounded-md border border-gray-300 bg-white p-2">
                {DEPARTMENTS.map((d) => (
                  <label
                    key={d.name}
                    className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-gray-50"
                  >
                    <input
                      type="checkbox"
                      checked={selectedDepts.includes(d.name)}
                      onChange={() => toggleDept(d.name)}
                      className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                    />
                    <span className="text-sm text-gray-800">{d.name}</span>
                    <span className="ml-auto text-xs text-gray-500">
                      {d.users} users
                    </span>
                  </label>
                ))}
              </div>
            )}

            {mode === 'custom' && (
              <textarea
                value={customList}
                onChange={(e) => setCustomList(e.target.value)}
                placeholder="One email address per line"
                rows={4}
                className="mt-3 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            )}
          </fieldset>

          {/* Channel */}
          <fieldset>
            <legend className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Delivery channel
            </legend>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(['slack', 'email'] as const).map((c) => {
                const active = channel === c;
                return (
                  <button
                    type="button"
                    key={c}
                    onClick={() => setChannel(c)}
                    className={
                      active
                        ? 'rounded-md border border-primary-500 bg-primary-50 px-3 py-2 text-left ring-1 ring-primary-200'
                        : 'rounded-md border border-gray-200 bg-white px-3 py-2 text-left hover:border-gray-300'
                    }
                  >
                    <p className="text-sm font-medium text-gray-900">
                      {c === 'slack' ? 'Slack DM' : 'Email fallback'}
                    </p>
                    <p className="mt-0.5 text-[11px] text-gray-600">
                      {c === 'slack'
                        ? 'Block Kit interactive — fastest completion'
                        : 'Magic-link to hosted survey'}
                    </p>
                  </button>
                );
              })}
            </div>
          </fieldset>

          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100">
              {error}
            </p>
          )}
        </div>

        <div className="mt-6 flex items-center justify-end gap-2 border-t border-gray-100 pt-4">
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
            className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
          >
            Launch Campaign
          </button>
        </div>
      </div>
    </div>
  );
}
