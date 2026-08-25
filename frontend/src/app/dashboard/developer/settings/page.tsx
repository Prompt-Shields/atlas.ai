'use client';

import { useEffect, useState } from 'react';
import {
  ALL_SCOPES,
  ALL_SEVERITIES,
  developerApi,
  type DeveloperAPIKey,
  type DeveloperScope,
  type EventDefinition,
  type EventSeverity,
} from '@/lib/developer';

type Tab = 'keys' | 'event-types';

export default function DeveloperSettingsPage() {
  const [tab, setTab] = useState<Tab>('keys');

  return (
    <div className="p-8 max-w-5xl">
      <h1 className="text-3xl font-semibold mb-2">Developer Settings</h1>
      <p className="text-gray-600 mb-6">
        Manage the API keys and event types your developers use to send
        telemetry into Atlas AI.
      </p>

      <div className="border-b border-gray-200 mb-6">
        <nav className="flex gap-6 -mb-px">
          {(['keys', 'event-types'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              aria-pressed={tab === t}
              className={`pb-3 px-1 border-b-2 text-sm font-medium ${
                tab === t
                  ? 'border-indigo-500 text-indigo-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t === 'keys' ? 'API Keys' : 'Event Types'}
            </button>
          ))}
        </nav>
      </div>

      {tab === 'keys' ? <KeysTab /> : <EventTypesTab />}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// API Keys tab
// ──────────────────────────────────────────────────────────────────────

function KeysTab() {
  const [keys, setKeys] = useState<DeveloperAPIKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await developerApi.listKeys(true);
      setKeys(res.keys);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const handleRevoke = async (id: string) => {
    if (!confirm('Revoke this key? Existing integrations using it will stop working.')) return;
    try {
      await developerApi.revokeKey(id);
      await refresh();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <div>
      {createdKey && (
        <div className="mb-4 p-4 border border-amber-300 bg-amber-50 rounded">
          <div className="font-semibold text-amber-900 mb-2">
            Your new API key — copy it now. You won&apos;t see it again.
          </div>
          <code className="block bg-white p-2 rounded border text-sm font-mono break-all">
            {createdKey}
          </code>
          <button
            onClick={() => setCreatedKey(null)}
            className="mt-3 text-sm text-amber-700 underline"
          >
            I&apos;ve copied it
          </button>
        </div>
      )}

      <div className="flex justify-between items-center mb-4">
        <p className="text-sm text-gray-600">{keys.length} keys</p>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700"
        >
          + New API key
        </button>
      </div>

      {showCreate && (
        <CreateKeyForm
          onClose={() => setShowCreate(false)}
          onCreated={(full_key) => {
            setShowCreate(false);
            setCreatedKey(full_key);
            void refresh();
          }}
        />
      )}

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="text-red-600">{error}</p>
      ) : keys.length === 0 ? (
        <p className="text-gray-500 italic">No keys yet — create one above.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b">
              <th className="py-2">Name</th>
              <th className="py-2">Prefix</th>
              <th className="py-2">Scopes</th>
              <th className="py-2">Status</th>
              <th className="py-2">Created</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id} className="border-b">
                <td className="py-2">
                  <div className="font-medium">{k.name}</div>
                  {k.description && (
                    <div className="text-xs text-gray-500">{k.description}</div>
                  )}
                </td>
                <td className="py-2 font-mono text-xs">{k.key_prefix}…</td>
                <td className="py-2">
                  <div className="flex flex-wrap gap-1">
                    {k.scopes.map((s) => (
                      <span
                        key={s}
                        className="text-xs px-1.5 py-0.5 bg-gray-100 rounded"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="py-2">
                  {k.is_active ? (
                    <span className="text-green-600">Active</span>
                  ) : (
                    <span className="text-gray-400">Revoked</span>
                  )}
                </td>
                <td className="py-2 text-gray-500 text-xs">
                  {new Date(k.created_at).toLocaleDateString()}
                </td>
                <td className="py-2 text-right">
                  {k.is_active && (
                    <button
                      onClick={() => handleRevoke(k.id)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CreateKeyForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (fullKey: string) => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [scopes, setScopes] = useState<Set<DeveloperScope>>(
    new Set<DeveloperScope>(['events:write']),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleScope = (s: DeveloperScope) => {
    const next = new Set(scopes);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    setScopes(next);
  };

  const submit = async () => {
    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    if (scopes.size === 0) {
      setError('Select at least one scope');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await developerApi.createKey({
        name: name.trim(),
        description: description.trim() || null,
        scopes: Array.from(scopes),
      });
      onCreated(res.full_key);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mb-6 p-4 border border-gray-200 rounded">
      <h3 className="font-semibold mb-3">Create API key</h3>
      <div className="space-y-3">
        <div>
          <label className="block text-sm font-medium mb-1">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. ci-staging-events"
            className="w-full px-3 py-1.5 border rounded text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Description (optional)
          </label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full px-3 py-1.5 border rounded text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Scopes</label>
          <div className="grid grid-cols-2 gap-2">
            {ALL_SCOPES.map((s) => (
              <label key={s} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={scopes.has(s)}
                  onChange={() => toggleScope(s)}
                />
                <code>{s}</code>
              </label>
            ))}
          </div>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={submit}
            disabled={submitting}
            className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
          >
            {submitting ? 'Creating…' : 'Create'}
          </button>
          <button
            onClick={onClose}
            className="px-3 py-1.5 border rounded text-sm hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Event Types tab
// ──────────────────────────────────────────────────────────────────────

function EventTypesTab() {
  const [defs, setDefs] = useState<EventDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await developerApi.listEventTypes(true);
      setDefs(res.definitions);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const toggleEnabled = async (d: EventDefinition) => {
    try {
      await developerApi.updateEventType(d.id, { is_enabled: !d.is_enabled });
      await refresh();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const handleDelete = async (d: EventDefinition) => {
    if (!confirm(`Delete event type "${d.name}"? Events already ingested are kept.`)) return;
    try {
      await developerApi.deleteEventType(d.id);
      await refresh();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <p className="text-sm text-gray-600">{defs.length} event types</p>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700"
        >
          + New event type
        </button>
      </div>

      {showCreate && (
        <CreateEventTypeForm
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            void refresh();
          }}
        />
      )}

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="text-red-600">{error}</p>
      ) : defs.length === 0 ? (
        <p className="text-gray-500 italic">
          No event types yet — create one above. Developers can only ingest
          events for types you&apos;ve defined.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b">
              <th className="py-2">Name</th>
              <th className="py-2">Description</th>
              <th className="py-2">Default severity</th>
              <th className="py-2">Status</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody>
            {defs.map((d) => (
              <tr key={d.id} className="border-b">
                <td className="py-2 font-mono text-xs">{d.name}</td>
                <td className="py-2 text-gray-700">
                  {d.description || (
                    <span className="text-gray-400">—</span>
                  )}
                </td>
                <td className="py-2">
                  <SeverityBadge severity={d.default_severity} />
                </td>
                <td className="py-2">
                  {d.is_enabled ? (
                    <span className="text-green-600">Enabled</span>
                  ) : (
                    <span className="text-gray-400">Disabled</span>
                  )}
                </td>
                <td className="py-2 text-right space-x-3">
                  <button
                    onClick={() => toggleEnabled(d)}
                    className="text-xs text-indigo-600 hover:underline"
                  >
                    {d.is_enabled ? 'Disable' : 'Enable'}
                  </button>
                  <button
                    onClick={() => handleDelete(d)}
                    className="text-xs text-red-600 hover:underline"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CreateEventTypeForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [severity, setSeverity] = useState<EventSeverity>('info');
  const [schemaJson, setSchemaJson] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!/^[a-z][a-z0-9._-]*$/.test(name)) {
      setError('Name must be lowercase, start with a letter, and may include . _ -');
      return;
    }

    let schema: Record<string, unknown> | null = null;
    if (schemaJson.trim()) {
      try {
        schema = JSON.parse(schemaJson);
      } catch {
        setError('Schema must be valid JSON');
        return;
      }
    }

    setSubmitting(true);
    setError(null);
    try {
      await developerApi.createEventType({
        name,
        description: description.trim() || null,
        default_severity: severity,
        schema,
      });
      onCreated();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mb-6 p-4 border border-gray-200 rounded">
      <h3 className="font-semibold mb-3">Define event type</h3>
      <div className="space-y-3">
        <div>
          <label className="block text-sm font-medium mb-1">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. agent.task_started"
            className="w-full px-3 py-1.5 border rounded text-sm font-mono"
          />
          <p className="text-xs text-gray-500 mt-1">
            Lowercase, may contain <code>. _ -</code>. Conventional format:{' '}
            <code>domain.action</code>.
          </p>
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Description</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full px-3 py-1.5 border rounded text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Default severity
          </label>
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as EventSeverity)}
            className="w-full px-3 py-1.5 border rounded text-sm"
          >
            {ALL_SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Schema (optional, JSON)
          </label>
          <textarea
            value={schemaJson}
            onChange={(e) => setSchemaJson(e.target.value)}
            placeholder='{"task_id": "string", "agent": "string"}'
            rows={4}
            className="w-full px-3 py-1.5 border rounded text-sm font-mono"
          />
          <p className="text-xs text-gray-500 mt-1">
            Advisory only — the API accepts any payload. Helps developers know
            what to send.
          </p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={submit}
            disabled={submitting}
            className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
          >
            {submitting ? 'Creating…' : 'Create'}
          </button>
          <button
            onClick={onClose}
            className="px-3 py-1.5 border rounded text-sm hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────

function SeverityBadge({ severity }: { severity: EventSeverity }) {
  const color = {
    debug: 'bg-gray-100 text-gray-700',
    info: 'bg-blue-100 text-blue-800',
    warning: 'bg-amber-100 text-amber-800',
    error: 'bg-red-100 text-red-800',
    critical: 'bg-red-200 text-red-900 font-bold',
  }[severity];
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${color}`}>{severity}</span>
  );
}
