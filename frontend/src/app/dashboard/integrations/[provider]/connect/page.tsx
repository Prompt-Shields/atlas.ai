'use client';

// ─────────────────────────────────────────────────────────────────────
// /dashboard/integrations/[provider]/connect
//
// MDM connect form for the non-OAuth providers (Jamf / Kandji /
// JumpCloud). OAuth providers (Microsoft, Slack) skip this page —
// the Integrations grid's Connect button opens their authorize URL
// directly.
//
// The form posts to the v0.3 dedicated connect endpoints
// (PR #49) which live-verify credentials before persisting and
// Fernet-encrypt server-side so the cleartext password / token
// never round-trips back via the dashboard.
//
//   Jamf:      POST /api/v1/integrations/jamf/connect
//   Kandji:    POST /api/v1/integrations/kandji/connect
//   JumpCloud: POST /api/v1/integrations/jumpcloud/connect
// ─────────────────────────────────────────────────────────────────────

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import {
  INTEGRATION_CATALOGUE,
  type IntegrationCard,
} from '@/lib/curated-demo-data';

const MDM_PROVIDERS = new Set(['JAMF_PRO', 'KANDJI', 'JUMPCLOUD']);

// ─── Cost providers ──────────────────────────────────────────────────
//
// The pull-mode AI spend providers all submit a small set of flat
// fields, so they are described declaratively and rendered by one
// component rather than a hand-written form each. The MDM forms above
// keep their bespoke components — their field help is more involved.
//
//   Anthropic: POST /api/v1/integrations/anthropic/connect
//   OpenAI:    POST /api/v1/integrations/openai/connect
//   Cursor:    POST /api/v1/integrations/cursor/connect
//   Copilot:   POST /api/v1/integrations/github-copilot/connect
//   Vercel:    POST /api/v1/integrations/vercel/connect

type FieldKind = 'text' | 'password' | 'number' | 'checkbox';

interface FieldSpec {
  name: string;
  label: string;
  kind: FieldKind;
  required?: boolean;
  placeholder?: string;
  help?: string;
  minLength?: number;
  /** Render only when this checkbox field is ticked. */
  showWhen?: string;
}

type FieldValues = Record<string, string | boolean>;

interface CostProviderSpec {
  fields: FieldSpec[];
  submit: (values: FieldValues) => Promise<unknown>;
}

/** Trimmed string value, or '' when unset. */
function text(values: FieldValues, name: string): string {
  const v = values[name];
  return typeof v === 'string' ? v.trim() : '';
}

/** Include a key only when the admin actually filled it in. */
function optional(values: FieldValues, name: string): Record<string, string> {
  const v = text(values, name);
  return v ? { [name]: v } : {};
}

const API_KEY_FIELD: FieldSpec = {
  name: 'api_key',
  label: 'Admin API key',
  kind: 'password',
  required: true,
  minLength: 10,
  help: 'Fernet-encrypted server-side. Never round-trips back via the API.',
};

const COST_PROVIDERS: Record<string, CostProviderSpec> = {
  ANTHROPIC: {
    fields: [
      {
        ...API_KEY_FIELD,
        placeholder: 'sk-ant-admin-…',
        help: 'Anthropic Console → Settings → Admin keys. Needs organization-level billing access, not a regular API key.',
      },
    ],
    submit: (v) => api.anthropicConnect({ api_key: text(v, 'api_key') }),
  },
  OPENAI: {
    fields: [
      {
        ...API_KEY_FIELD,
        placeholder: 'sk-admin-…',
        help: 'OpenAI platform → Organization → Admin keys. Covers API spend only — ChatGPT seat billing is not exposed over the API.',
      },
    ],
    submit: (v) => api.openaiConnect({ api_key: text(v, 'api_key') }),
  },
  CURSOR: {
    fields: [
      {
        ...API_KEY_FIELD,
        label: 'Teams Admin API key',
        help: 'Cursor dashboard → Settings → Teams → Admin API. Returns per-member on-demand spend.',
      },
    ],
    submit: (v) => api.cursorConnect({ api_key: text(v, 'api_key') }),
  },
  GITHUB_COPILOT: {
    fields: [
      {
        ...API_KEY_FIELD,
        label: 'GitHub access token',
        placeholder: 'ghp_… or github_pat_…',
        help: 'Needs the manage_billing:copilot scope for the organization below.',
      },
      {
        name: 'github_org',
        label: 'GitHub organization',
        kind: 'text',
        required: true,
        placeholder: 'your-org',
        help: 'The org whose Copilot seats are billed. Pasting the full org URL is fine.',
      },
      {
        name: 'seat_price_usd',
        label: 'Per-seat monthly price (USD)',
        kind: 'number',
        placeholder: '19.00',
        help: 'Optional. GitHub exposes no dollar figure over the API, so spend is derived as seats × price. Leave blank to use the documented default.',
      },
    ],
    submit: (v) =>
      api.copilotConnect({
        api_key: text(v, 'api_key'),
        github_org: text(v, 'github_org'),
        ...optional(v, 'seat_price_usd'),
      }),
  },
  VERCEL: {
    fields: [
      {
        ...API_KEY_FIELD,
        label: 'Vercel access token',
        help: 'Vercel → Account Settings → Tokens. Scope it to the team below.',
      },
      {
        name: 'team_slug',
        label: 'Team slug',
        kind: 'text',
        placeholder: 'acme',
        help: 'Optional. Leave both team fields blank for a personal account.',
      },
      {
        name: 'team_id',
        label: 'Team ID',
        kind: 'text',
        placeholder: 'team_…',
        help: 'Optional. Use when the slug is ambiguous.',
      },
      {
        name: 'ai_gateway',
        label: 'Also ingest AI Gateway model spend',
        kind: 'checkbox',
        help: 'Adds per-model token spend routed through Vercel AI Gateway.',
      },
      {
        name: 'ai_gateway_key',
        label: 'AI Gateway key',
        kind: 'password',
        showWhen: 'ai_gateway',
        help: 'Optional — the access token above is used when blank. Stored encrypted, never returned.',
      },
    ],
    submit: (v) =>
      api.vercelConnect({
        api_key: text(v, 'api_key'),
        ...optional(v, 'team_slug'),
        ...optional(v, 'team_id'),
        ai_gateway: v.ai_gateway === true,
        ...(v.ai_gateway === true ? optional(v, 'ai_gateway_key') : {}),
      }),
  },
};

const CONNECTABLE = new Set([...MDM_PROVIDERS, ...Object.keys(COST_PROVIDERS)]);

// ─── Per-provider form ───────────────────────────────────────────────


interface JamfForm {
  server_url: string;
  username: string;
  password: string;
}

interface KandjiForm {
  base_url: string;
  api_token: string;
}

interface JumpcloudForm {
  api_key: string;
}

function isUrl(s: string): boolean {
  try {
    const u = new URL(s);
    return u.protocol === 'https:' || u.protocol === 'http:';
  } catch {
    return false;
  }
}

// ─── Form components ─────────────────────────────────────────────────


const INPUT_CLASS =
  'mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50';

function CostFormFields({
  spec,
  values,
  onChange,
  disabled,
}: {
  spec: CostProviderSpec;
  values: FieldValues;
  onChange: (next: FieldValues) => void;
  disabled: boolean;
}) {
  return (
    <>
      {spec.fields
        .filter((f) => !f.showWhen || values[f.showWhen] === true)
        .map((f) =>
          f.kind === 'checkbox' ? (
            <label key={f.name} className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={values[f.name] === true}
                onChange={(e) =>
                  onChange({ ...values, [f.name]: e.target.checked })
                }
                disabled={disabled}
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
              />
              <span>
                <span className="text-sm text-gray-900">{f.label}</span>
                {f.help && (
                  <span className="mt-0.5 block text-[11px] text-gray-500">
                    {f.help}
                  </span>
                )}
              </span>
            </label>
          ) : (
            <label key={f.name} className="block">
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                {f.label}
                {!f.required && (
                  <span className="ml-1 font-normal normal-case text-gray-400">
                    (optional)
                  </span>
                )}
              </span>
              <input
                type={f.kind}
                required={f.required}
                autoComplete={f.kind === 'password' ? 'new-password' : 'off'}
                step={f.kind === 'number' ? '0.01' : undefined}
                min={f.kind === 'number' ? '0' : undefined}
                value={typeof values[f.name] === 'string' ? (values[f.name] as string) : ''}
                onChange={(e) =>
                  onChange({ ...values, [f.name]: e.target.value })
                }
                disabled={disabled}
                placeholder={f.placeholder}
                className={INPUT_CLASS}
              />
              {f.help && (
                <p className="mt-1 text-[11px] text-gray-500">{f.help}</p>
              )}
            </label>
          ),
        )}
    </>
  );
}

/** Client-side pre-flight. The backend does the real auth check. */
function validateCostFields(spec: CostProviderSpec, values: FieldValues): string | null {
  for (const f of spec.fields) {
    if (f.showWhen && values[f.showWhen] !== true) continue;
    if (f.kind === 'checkbox') continue;
    const value = text(values, f.name);
    if (f.required && !value) return `${f.label} is required`;
    if (value && f.minLength && value.length < f.minLength) {
      return `${f.label} looks too short — paste the full value`;
    }
    if (f.kind === 'number' && value && !(Number(value) > 0)) {
      return `${f.label} must be a positive number`;
    }
  }
  return null;
}

function JamfFormFields({
  values,
  onChange,
  disabled,
}: {
  values: JamfForm;
  onChange: (next: JamfForm) => void;
  disabled: boolean;
}) {
  return (
    <>
      <label className="block">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Jamf Pro server URL
        </span>
        <input
          type="url"
          required
          value={values.server_url}
          onChange={(e) =>
            onChange({ ...values, server_url: e.target.value })
          }
          disabled={disabled}
          placeholder="https://yourorg.jamfcloud.com"
          className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
        />
        <p className="mt-1 text-[11px] text-gray-500">
          Full URL of your Jamf Pro instance. No trailing slash.
        </p>
      </label>
      <label className="block">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          API service account — username
        </span>
        <input
          type="text"
          required
          autoComplete="off"
          value={values.username}
          onChange={(e) =>
            onChange({ ...values, username: e.target.value })
          }
          disabled={disabled}
          className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
        />
        <p className="mt-1 text-[11px] text-gray-500">
          Create a dedicated service account in Jamf Pro with read-only
          roles for Computer Inventory + Computer Groups.
        </p>
      </label>
      <label className="block">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          API service account — password
        </span>
        <input
          type="password"
          required
          autoComplete="new-password"
          value={values.password}
          onChange={(e) =>
            onChange({ ...values, password: e.target.value })
          }
          disabled={disabled}
          className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
        />
        <p className="mt-1 text-[11px] text-gray-500">
          Fernet-encrypted server-side. Never round-trips back via the API.
        </p>
      </label>
    </>
  );
}

function KandjiFormFields({
  values,
  onChange,
  disabled,
}: {
  values: KandjiForm;
  onChange: (next: KandjiForm) => void;
  disabled: boolean;
}) {
  return (
    <>
      <label className="block">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Kandji API base URL
        </span>
        <input
          type="url"
          required
          value={values.base_url}
          onChange={(e) =>
            onChange({ ...values, base_url: e.target.value })
          }
          disabled={disabled}
          placeholder="https://yourorg.api.kandji.io"
          className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
        />
        <p className="mt-1 text-[11px] text-gray-500">
          Your tenant&apos;s API subdomain. No trailing slash.
        </p>
      </label>
      <label className="block">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          API token
        </span>
        <input
          type="password"
          required
          autoComplete="new-password"
          value={values.api_token}
          onChange={(e) =>
            onChange({ ...values, api_token: e.target.value })
          }
          disabled={disabled}
          className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
        />
        <p className="mt-1 text-[11px] text-gray-500">
          Create at Kandji admin → Settings → Access → API Token.
          Read-only scope for Devices + Blueprints is sufficient.
        </p>
      </label>
    </>
  );
}

function JumpcloudFormFields({
  values,
  onChange,
  disabled,
}: {
  values: JumpcloudForm;
  onChange: (next: JumpcloudForm) => void;
  disabled: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        JumpCloud API key
      </span>
      <input
        type="password"
        required
        autoComplete="new-password"
        value={values.api_key}
        onChange={(e) => onChange({ ...values, api_key: e.target.value })}
        disabled={disabled}
        className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-gray-50"
      />
      <p className="mt-1 text-[11px] text-gray-500">
        Create at JumpCloud console → Settings → API Settings.
        Read-only access to Systems + System Groups is sufficient.
      </p>
    </label>
  );
}

// ─── Page ────────────────────────────────────────────────────────────


export default function MdmConnectPage() {
  const params = useParams<{ provider: string }>();
  const router = useRouter();
  const providerSlug = useMemo(() => {
    const raw = Array.isArray(params.provider)
      ? params.provider[0]
      : params.provider;
    return (raw || '').toUpperCase();
  }, [params.provider]);

  const card: IntegrationCard | undefined = useMemo(
    () =>
      INTEGRATION_CATALOGUE.find((c) => c.meta.provider === providerSlug),
    [providerSlug],
  );

  const [jamf, setJamf] = useState<JamfForm>({
    server_url: '',
    username: '',
    password: '',
  });
  const [kandji, setKandji] = useState<KandjiForm>({
    base_url: '',
    api_token: '',
  });
  const [jumpcloud, setJumpcloud] = useState<JumpcloudForm>({
    api_key: '',
  });
  const [costFields, setCostFields] = useState<FieldValues>({});
  const costSpec = COST_PROVIDERS[providerSlug];

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Show a clear "not applicable" panel for OAuth providers if
  // someone deep-links to this route.
  if (!card) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          Provider not found
        </h1>
        <Link
          href="/dashboard/integrations"
          className="mt-2 inline-block text-sm font-medium text-primary-600 hover:text-primary-700"
        >
          ← Back to Integrations
        </Link>
      </div>
    );
  }

  if (!CONNECTABLE.has(providerSlug)) {
    return (
      <div>
        <Link
          href={`/dashboard/integrations`}
          className="text-xs font-medium text-gray-500 hover:text-gray-700"
        >
          ← Integrations
        </Link>
        <h1 className="mt-1 text-2xl font-bold text-gray-900">
          Use the standard install flow
        </h1>
        <p className="mt-3 max-w-prose text-sm text-gray-700">
          {card.meta.displayName} does not use credential entry. Open the
          Integrations grid and click{' '}
          <strong>Connect</strong> on the {card.meta.displayName} card
          to start the authorization flow.
        </p>
        <Link
          href="/dashboard/integrations"
          className="mt-4 inline-block rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700"
        >
          Open Integrations →
        </Link>
      </div>
    );
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);

    // Client-side pre-flight: URL shape + non-empty fields. The
    // backend does the real auth check.
    let resultPromise: Promise<unknown>;
    if (providerSlug === 'JAMF_PRO') {
      if (!isUrl(jamf.server_url)) {
        setError('Server URL must include https://');
        return;
      }
      if (!jamf.username || !jamf.password) {
        setError('Username and password are required');
        return;
      }
      resultPromise = api.jamfConnect(jamf);
    } else if (providerSlug === 'KANDJI') {
      if (!isUrl(kandji.base_url)) {
        setError('Base URL must include https://');
        return;
      }
      if (kandji.api_token.length < 10) {
        setError('API token looks too short — paste the full value');
        return;
      }
      resultPromise = api.kandjiConnect(kandji);
    } else if (providerSlug === 'JUMPCLOUD') {
      if (jumpcloud.api_key.length < 10) {
        setError('API key looks too short — paste the full value');
        return;
      }
      resultPromise = api.jumpcloudConnect(jumpcloud);
    } else if (costSpec) {
      const problem = validateCostFields(costSpec, costFields);
      if (problem) {
        setError(problem);
        return;
      }
      resultPromise = costSpec.submit(costFields);
    } else {
      setError(`Unknown provider ${providerSlug}`);
      return;
    }

    setSubmitting(true);
    try {
      await resultPromise;
      setToast(`✓ ${card.meta.displayName} connected`);
      // Brief delay so the toast lands before the route change.
      window.setTimeout(() => {
        router.push(`/dashboard/integrations/${providerSlug}`);
      }, 900);
    } catch (e) {
      // Backend returns 401 with a structured error message on bad
      // credentials. Surface that directly to the admin.
      const message =
        e instanceof Error && e.message
          ? e.message
          : 'Connection failed — check the values and try again.';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <Link
        href={`/dashboard/integrations`}
        className="text-xs font-medium text-gray-500 hover:text-gray-700"
      >
        ← Integrations
      </Link>
      <h1 className="mt-1 text-2xl font-bold text-gray-900">
        Connect {card.meta.displayName}
      </h1>
      <p className="mt-1 text-sm text-gray-600">{card.meta.description}</p>

      <form
        onSubmit={handleSubmit}
        className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200"
      >
        <h2 className="text-sm font-semibold text-gray-900">Credentials</h2>
        <p className="mt-1 text-xs text-gray-500">
          Atlas verifies these against {card.meta.shortName} once before
          encrypting them at rest. Cleartext credentials never round-trip
          back via the API after this initial submission.
        </p>

        <div className="mt-5 space-y-4">
          {providerSlug === 'JAMF_PRO' && (
            <JamfFormFields
              values={jamf}
              onChange={setJamf}
              disabled={submitting}
            />
          )}
          {providerSlug === 'KANDJI' && (
            <KandjiFormFields
              values={kandji}
              onChange={setKandji}
              disabled={submitting}
            />
          )}
          {providerSlug === 'JUMPCLOUD' && (
            <JumpcloudFormFields
              values={jumpcloud}
              onChange={setJumpcloud}
              disabled={submitting}
            />
          )}
          {costSpec && (
            <CostFormFields
              spec={costSpec}
              values={costFields}
              onChange={setCostFields}
              disabled={submitting}
            />
          )}
        </div>

        {error && (
          <p
            role="alert"
            className="mt-5 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-100"
          >
            {error}
          </p>
        )}

        <div className="mt-6 flex items-center justify-end gap-2 border-t border-gray-100 pt-5">
          <Link
            href="/dashboard/integrations"
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {submitting ? 'Verifying…' : 'Connect & save'}
          </button>
        </div>
      </form>

      {MDM_PROVIDERS.has(providerSlug) && (
      <p className="mt-4 text-xs text-gray-500">
        After connecting,{' '}
        <Link
          href={`/dashboard/integrations/${providerSlug}/deployment`}
          className="font-medium text-primary-600 hover:text-primary-700"
        >
          generate a deployment profile
        </Link>{' '}
        to push the Promptly extension to your managed devices.
      </p>
      )}
      {costSpec && (
        <p className="mt-4 text-xs text-gray-500">
          Atlas verifies the key by running one real billing fetch before
          saving it, so a successful connect means the nightly cost sync
          will work too. Spend appears in{' '}
          <Link
            href="/dashboard/ai-spend"
            className="font-medium text-primary-600 hover:text-primary-700"
          >
            AI spend
          </Link>{' '}
          after the next sync.
        </p>
      )}

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
