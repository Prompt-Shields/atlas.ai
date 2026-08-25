'use client';

// ─────────────────────────────────────────────────────────────────────
// Per-provider integration detail + config form.
//
// Route: /dashboard/integrations/[provider]
// Shows the connected integration's metadata + an editable config
// form whose shape varies per provider (Intune device-group filter,
// Purview classifier picker, Defender CASB app filter, etc.).
//
// All providers share three controls:
//   1. Display name (free text)
//   2. is_active toggle
//   3. Disconnect (clears tokens, status → DISABLED)
//
// Provider-specific config goes into the JSON `config_json` column
// via PATCH /api/v1/integrations/{id}. The form schema for each
// provider is defined locally so it's obvious what each tenant can
// tune; the backend doesn't validate the JSON shape today (will
// when each provider's worker reads it).
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import type {
  IntegrationCardResponse,
  IntegrationProvider,
} from '@/lib/types';

// ─── Provider config form schemas ────────────────────────────────────

interface ConfigField {
  key: string;
  label: string;
  help?: string;
  type: 'text' | 'textarea' | 'boolean' | 'list';
}

const CONFIG_FIELDS: Record<string, ConfigField[]> = {
  MICROSOFT_ENTRA_ID: [
    {
      key: 'sync_disabled_users',
      label: 'Sync disabled accounts',
      help: 'When on, includes disabled Entra ID accounts in the directory pull (recommended off — audit-history-only).',
      type: 'boolean',
    },
    {
      key: 'department_filter',
      label: 'Department filter (optional)',
      help: 'Comma-separated list to restrict sync to certain departments. Leave empty for the full tenant.',
      type: 'list',
    },
  ],
  MICROSOFT_INTUNE: [
    {
      key: 'device_group_ids',
      label: 'Device group IDs (Intune)',
      help: 'Comma-separated Intune device-group object IDs. Policies push only to enrolled members of these groups.',
      type: 'list',
    },
    {
      key: 'platform_filter',
      label: 'Platform filter',
      help: 'One of: windows, macos, all',
      type: 'text',
    },
    {
      key: 'allow_write',
      label: 'Allow policy push',
      help: 'Required for the /dashboard/policies *Push via Intune* affordance. Off = read-only.',
      type: 'boolean',
    },
  ],
  MICROSOFT_PURVIEW: [
    {
      key: 'classifier_ids',
      label: 'Sensitive classifier IDs',
      help: 'Comma-separated Purview classifier IDs to ingest events for. Leave empty for all classifiers.',
      type: 'list',
    },
    {
      key: 'event_lookback_days',
      label: 'Initial backfill window (days)',
      help: 'On first sync, pull this many days of historical DLP events. Default 30.',
      type: 'text',
    },
  ],
  MICROSOFT_DEFENDER_CASB: [
    {
      key: 'app_filter',
      label: 'App filter (domains)',
      help: 'Comma-separated app domains to track (e.g. chatgpt.com, claude.ai). Empty = all detected apps.',
      type: 'list',
    },
    {
      key: 'severity_floor',
      label: 'Severity floor',
      help: 'One of: low, medium, high. Events below this severity are not ingested.',
      type: 'text',
    },
  ],
  SLACK: [
    {
      key: 'survey_throttle_days',
      label: 'Min days between surveys per user',
      help: 'Suppresses sending a fresh survey if the user already received one within this window. Default 14.',
      type: 'text',
    },
    {
      key: 'bot_display_name',
      label: 'Bot display name',
      help: "Shown in Slack DMs. Default: 'Atlas — AI governance'.",
      type: 'text',
    },
  ],
  JAMF_PRO: [
    {
      key: 'server_url',
      label: 'Jamf Pro server URL',
      help: 'e.g. https://yourorg.jamfcloud.com — no trailing slash.',
      type: 'text',
    },
    {
      key: 'username',
      label: 'Jamf API service-account username',
      help: 'Create a dedicated service account in Jamf Pro with read-only roles for Computer Inventory + Computer Groups.',
      type: 'text',
    },
    {
      key: 'password',
      label: 'Jamf API service-account password',
      help: "Stored Fernet-encrypted at rest. v0.2 — admin enters here; v0.3 ships a dedicated /integrations/jamf/connect endpoint that encrypts server-side without round-tripping the value.",
      type: 'text',
    },
  ],
  KANDJI: [
    {
      key: 'base_url',
      label: 'Kandji API base URL',
      help: 'Your tenant subdomain, e.g. https://yourorg.api.kandji.io — no trailing slash.',
      type: 'text',
    },
    {
      key: 'api_token',
      label: 'Kandji API token',
      help: 'Create at Kandji admin → Settings → Access → API Token. Read-only scope for Devices + Blueprints is sufficient.',
      type: 'text',
    },
  ],
  JUMPCLOUD: [
    {
      key: 'api_key',
      label: 'JumpCloud API key',
      help: 'Create at JumpCloud console → Settings → API Settings. Read-only access to Systems + System Groups is sufficient for the v0.2 sync.',
      type: 'text',
    },
  ],
};

// ─── Helpers ─────────────────────────────────────────────────────────

function fieldsForProvider(provider: string): ConfigField[] {
  return CONFIG_FIELDS[provider] || [];
}

function parseFieldValue(
  field: ConfigField,
  raw: string | boolean,
): unknown {
  if (field.type === 'boolean') return Boolean(raw);
  if (field.type === 'list') {
    return String(raw)
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return String(raw).trim();
}

function serialiseFieldValue(value: unknown, field: ConfigField): string {
  if (field.type === 'boolean') return ''; // handled separately
  if (Array.isArray(value)) return value.join(', ');
  if (value == null) return '';
  return String(value);
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function IntegrationDetailPage() {
  const params = useParams<{ provider: string }>();
  const router = useRouter();
  const providerSlug = useMemo(() => {
    const raw = Array.isArray(params.provider)
      ? params.provider[0]
      : params.provider;
    return (raw || '').toUpperCase();
  }, [params.provider]);

  const [card, setCard] = useState<IntegrationCardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>(
    {},
  );
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const fields = useMemo(
    () => fieldsForProvider(providerSlug),
    [providerSlug],
  );

  // Load the integration record for this tenant + provider on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api.listIntegrations();
        const match = list.integrations.find(
          (i) => i.meta.provider === providerSlug,
        );
        if (cancelled) return;
        if (!match) {
          setError(`Provider ${providerSlug} not found`);
          return;
        }
        setCard(match);
        setDisplayName(match.display_name || match.meta.display_name);
        setIsActive(match.status !== 'DISABLED');
        // Hydrate the config form with the server's stored values
        // so the admin sees existing settings rather than empty inputs.
        if (match.config && typeof match.config === 'object') {
          setConfigValues(match.config as Record<string, unknown>);
        }
      } catch (e) {
        if (!cancelled) {
          setError(
            'Could not load integration. Try connecting first from the Integrations grid.',
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [providerSlug]);

  // When the card loads, hydrate config form (config_json is on the
  // server-side row but not on the IntegrationCardResponse — for now
  // the form starts empty and writes a fresh config_json on save).
  const setField = (key: string, value: unknown) =>
    setConfigValues((prev) => ({ ...prev, [key]: value }));

  const handleSave = async () => {
    if (!card || !card.integration_id) {
      setToast('Connect this integration before configuring it.');
      return;
    }
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      // Parse + normalise each field through its schema.
      const parsed: Record<string, unknown> = {};
      for (const field of fields) {
        const raw = configValues[field.key];
        if (raw === undefined || raw === '') continue;
        parsed[field.key] = parseFieldValue(field, raw as string | boolean);
      }
      payload.config_json = parsed;
      if (displayName.trim()) payload.display_name = displayName.trim();
      payload.is_active = isActive;

      await api.updateIntegration(card.integration_id, payload);
      setToast('✓ Saved');
    } catch {
      setToast('Save failed — check your connection and retry');
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    if (!card || !card.integration_id) return;
    if (
      !confirm(
        `Disconnect ${card.meta.short_name}? Tokens will be cleared; config preserved.`,
      )
    )
      return;
    try {
      await api.disconnectIntegration(card.integration_id);
      setToast(`${card.meta.short_name} disconnected`);
      // Brief delay so the user sees the toast.
      setTimeout(() => router.push('/dashboard/integrations'), 800);
    } catch {
      setToast('Disconnect failed');
    }
  };

  if (loading) {
    return (
      <div className="text-sm text-gray-500">Loading integration…</div>
    );
  }

  if (error || !card) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          Integration not found
        </h1>
        <p className="mt-2 text-sm text-gray-600">
          {error || 'No matching provider for the URL.'}
        </p>
        <Link
          href="/dashboard/integrations"
          className="mt-4 inline-block text-sm font-medium text-primary-600 hover:text-primary-700"
        >
          ← Back to Integrations
        </Link>
      </div>
    );
  }

  const notConnected =
    !card.integration_id || card.status === 'NOT_CONNECTED';

  return (
    <div>
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link
            href="/dashboard/integrations"
            className="text-xs font-medium text-gray-500 hover:text-gray-700"
          >
            ← Integrations
          </Link>
          <h1 className="mt-1 text-2xl font-bold text-gray-900">
            {card.meta.display_name}
          </h1>
          <p className="mt-1 text-sm text-gray-600">{card.meta.description}</p>
        </div>
        <span
          className={
            card.status === 'CONNECTED'
              ? 'rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200'
              : 'rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700 ring-1 ring-gray-200'
          }
        >
          {card.status}
        </span>
      </div>

      {/* MDM deployment-profile cross-link — only for device-category
          providers (Intune / Jamf / Kandji / JumpCloud). The page
          itself renders a clear "not applicable" notice for non-MDMs
          if someone deep-links, but the surface here is hidden to
          avoid clutter. */}
      {card.meta.category === 'device' && (
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-blue-50 px-5 py-4 ring-1 ring-blue-100">
          <p className="text-sm text-blue-900">
            <strong>Need to deploy the Promptly extension?</strong>{' '}
            Generate a ready-to-upload Configuration Profile for{' '}
            {card.meta.short_name}.
          </p>
          <Link
            href={`/dashboard/integrations/${providerSlug}/deployment`}
            className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
          >
            Open profile generator →
          </Link>
        </div>
      )}

      {/* Connection info card */}
      <div className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">Connection</h2>
        {notConnected ? (
          <p className="mt-2 text-sm text-gray-600">
            This integration isn&apos;t connected yet.{' '}
            <Link
              href="/dashboard/integrations"
              className="font-medium text-primary-600 hover:text-primary-700"
            >
              Connect from the Integrations grid
            </Link>{' '}
            to see and edit its configuration.
          </p>
        ) : (
          <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                {card.meta.vendor === 'microsoft'
                  ? 'Azure AD tenant'
                  : 'Workspace'}
              </dt>
              <dd className="mt-0.5 text-gray-900">
                {card.external_name || '—'}
              </dd>
              {card.external_id && (
                <dd className="mt-0.5 font-mono text-[11px] text-gray-500">
                  {card.external_id}
                </dd>
              )}
            </div>
            <div>
              <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                Connected
              </dt>
              <dd className="mt-0.5 text-gray-900">
                {card.connected_at
                  ? new Date(card.connected_at).toLocaleDateString()
                  : '—'}
              </dd>
              {card.last_synced_at && (
                <dd className="mt-0.5 text-xs text-gray-600">
                  Last sync{' '}
                  {new Date(card.last_synced_at).toLocaleString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </dd>
              )}
            </div>
            <div className="sm:col-span-2">
              <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                Granted scopes
              </dt>
              <dd className="mt-1 flex flex-wrap gap-1">
                {card.scopes.length === 0 ? (
                  <span className="text-xs text-gray-500">—</span>
                ) : (
                  card.scopes.map((s) => (
                    <span
                      key={s}
                      className="rounded-md bg-gray-50 px-2 py-0.5 font-mono text-[11px] text-gray-700 ring-1 ring-gray-100"
                    >
                      {s}
                    </span>
                  ))
                )}
              </dd>
            </div>
          </dl>
        )}
      </div>

      {/* Config form */}
      <div className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">Settings</h2>

        <label className="mt-4 block">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Display name
          </span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            disabled={notConnected}
            className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50 disabled:text-gray-400"
            placeholder={card.meta.display_name}
          />
        </label>

        <label className="mt-4 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
            disabled={notConnected}
            className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500 disabled:opacity-50"
          />
          <span className="text-gray-800">Integration is active</span>
        </label>
        <p className="text-[11px] text-gray-500">
          When off, syncs pause and downstream features (policy push, audience
          resolution) skip this provider.
        </p>

        {fields.length > 0 && (
          <div className="mt-6 border-t border-gray-100 pt-5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Provider-specific config
            </h3>
            <div className="mt-3 space-y-4">
              {fields.map((field) => (
                <div key={field.key}>
                  <label className="block">
                    <span className="text-xs font-semibold text-gray-700">
                      {field.label}
                    </span>
                    {field.type === 'boolean' ? (
                      <input
                        type="checkbox"
                        checked={Boolean(configValues[field.key])}
                        onChange={(e) =>
                          setField(field.key, e.target.checked)
                        }
                        disabled={notConnected}
                        className="ml-2 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                      />
                    ) : field.type === 'textarea' || field.type === 'list' ? (
                      <textarea
                        value={
                          serialiseFieldValue(
                            configValues[field.key],
                            field,
                          ) || ''
                        }
                        onChange={(e) =>
                          setField(field.key, e.target.value)
                        }
                        disabled={notConnected}
                        rows={field.type === 'list' ? 2 : 4}
                        className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
                        placeholder={
                          field.type === 'list' ? 'comma-separated' : ''
                        }
                      />
                    ) : (
                      <input
                        type="text"
                        value={
                          serialiseFieldValue(
                            configValues[field.key],
                            field,
                          ) || ''
                        }
                        onChange={(e) =>
                          setField(field.key, e.target.value)
                        }
                        disabled={notConnected}
                        className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
                      />
                    )}
                  </label>
                  {field.help && (
                    <p className="mt-1 text-[11px] text-gray-500">
                      {field.help}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-6 flex items-center justify-between gap-3 border-t border-gray-100 pt-5">
          {!notConnected && (
            <button
              type="button"
              onClick={handleDisconnect}
              className="text-xs font-medium text-red-600 hover:text-red-700"
            >
              Disconnect
            </button>
          )}
          <div className="ml-auto flex gap-2">
            <Link
              href="/dashboard/integrations"
              className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </Link>
            <button
              type="button"
              onClick={handleSave}
              disabled={notConnected || saving}
              className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      </div>

      {toast && (
        <div
          className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-xl"
          role="status"
          onAnimationEnd={() => {
            // Auto-dismiss after a moment.
            setTimeout(() => setToast(null), 3500);
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}
