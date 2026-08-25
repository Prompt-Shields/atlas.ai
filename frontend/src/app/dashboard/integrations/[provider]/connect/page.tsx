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

const CONNECTABLE = new Set(['JAMF_PRO', 'KANDJI', 'JUMPCLOUD']);

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
          {card.meta.displayName} connects via OAuth, not credential
          entry. Open the Integrations grid and click{' '}
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
