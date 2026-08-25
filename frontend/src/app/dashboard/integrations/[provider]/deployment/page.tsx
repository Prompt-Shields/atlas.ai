'use client';

// ─────────────────────────────────────────────────────────────────────
// /dashboard/integrations/[provider]/deployment
//
// MDM Configuration Profile generator UI. Renders the right
// XML/JSON/shell payload for the selected MDM, lets the admin
// download or copy. Implements v0.3 of docs/mdm-strategy.md — the
// IT admin can hand off the profile to their MDM today, before any
// per-MDM sync adapter ships.
//
// Provider sources of truth:
//   - frontend/src/lib/mdm-profiles.ts (pure functions, fully unit-
//     testable)
//   - frontend/src/lib/curated-demo-data.ts INTEGRATION_CATALOGUE
//     for the provider name / display
//
// This page only renders for device-category providers: Intune,
// Jamf, Kandji, JumpCloud. Non-device providers (Entra ID, Slack,
// Purview, Defender CASB) get a "no deployment profile needed for
// this integration" notice with a back-link.
// ─────────────────────────────────────────────────────────────────────

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import {
  INTEGRATION_CATALOGUE,
  type IntegrationCard,
} from '@/lib/curated-demo-data';
import {
  DEFAULT_ATLAS_ORIGIN,
  DEFAULT_EXTENSION_ID,
  profileFor,
  type ProfileBundle,
  type ProfileFile,
} from '@/lib/mdm-profiles';

// ─── Helpers ─────────────────────────────────────────────────────────

const DEVICE_MDMS = new Set([
  'MICROSOFT_INTUNE',
  'JAMF_PRO',
  'KANDJI',
  'JUMPCLOUD',
]);

function downloadFile(file: ProfileFile): void {
  const blob = new Blob([file.contents], { type: file.mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// ─── Sub-components ──────────────────────────────────────────────────

function StepList({ steps }: { steps: string[] }) {
  return (
    <ol className="mt-3 space-y-2 text-sm text-gray-700">
      {steps.map((s, i) => (
        <li key={i} className="flex gap-3">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary-100 text-[11px] font-semibold text-primary-700">
            {i + 1}
          </span>
          <span>{s}</span>
        </li>
      ))}
    </ol>
  );
}

function FileBlock({
  file,
  onCopied,
}: {
  file: ProfileFile;
  onCopied: (msg: string) => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
      <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-3 py-1.5 text-xs">
        <span className="font-mono text-gray-700">{file.filename}</span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={async () => {
              const ok = await copyToClipboard(file.contents);
              onCopied(ok ? '✓ Copied to clipboard' : 'Copy failed');
            }}
            className="rounded-md border border-gray-300 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-700 hover:bg-gray-100"
          >
            Copy
          </button>
          <button
            type="button"
            onClick={() => downloadFile(file)}
            className="rounded-md bg-primary-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-primary-700"
          >
            Download
          </button>
        </div>
      </div>
      <pre className="max-h-72 overflow-auto bg-gray-50 px-3 py-2 font-mono text-[11px] leading-relaxed text-gray-800">
        {file.contents}
      </pre>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function DeploymentProfilePage() {
  const params = useParams<{ provider: string }>();
  const providerSlug = useMemo(() => {
    const raw = Array.isArray(params.provider)
      ? params.provider[0]
      : params.provider;
    return (raw || '').toUpperCase();
  }, [params.provider]);

  // Lookup card metadata from the curated catalogue so we have the
  // display name + category even before the live integration row is
  // hydrated.
  const card: IntegrationCard | undefined = useMemo(
    () =>
      INTEGRATION_CATALOGUE.find((c) => c.meta.provider === providerSlug),
    [providerSlug],
  );

  const [extensionId, setExtensionId] = useState(DEFAULT_EXTENSION_ID);
  const [tenantHandle, setTenantHandle] = useState('sample-foundation');
  const [atlasOrigin, setAtlasOrigin] = useState(DEFAULT_ATLAS_ORIGIN);
  const [toast, setToast] = useState<string | null>(null);

  const bundle: ProfileBundle | null = useMemo(() => {
    if (!DEVICE_MDMS.has(providerSlug)) return null;
    return profileFor(providerSlug, {
      extensionId: extensionId.trim() || DEFAULT_EXTENSION_ID,
      tenantHandle: tenantHandle.trim() || 'tenant',
      atlasOrigin: atlasOrigin.trim() || DEFAULT_ATLAS_ORIGIN,
    });
  }, [providerSlug, extensionId, tenantHandle, atlasOrigin]);

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3000);
  };

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

  // Non-device provider — show a clear "nothing to deploy" panel.
  if (!DEVICE_MDMS.has(providerSlug)) {
    return (
      <div>
        <Link
          href={`/dashboard/integrations/${providerSlug}`}
          className="text-xs font-medium text-gray-500 hover:text-gray-700"
        >
          ← {card.meta.displayName}
        </Link>
        <h1 className="mt-1 text-2xl font-bold text-gray-900">
          No deployment profile needed
        </h1>
        <p className="mt-3 max-w-prose text-sm text-gray-700">
          {card.meta.displayName} is an{' '}
          <strong>API integration</strong>, not a device-management
          integration. Atlas talks to it over HTTPS using the OAuth
          token you granted — there&apos;s no endpoint software to push.
        </p>
        <p className="mt-3 max-w-prose text-sm text-gray-700">
          The deployment-profile generator is only available for MDM
          providers (Intune, Jamf Pro, Kandji, JumpCloud), which need
          to push the Promptly browser extension to managed devices.
          See{' '}
          <Link
            href="https://github.com/jun-bit-pulse-ai/atlas.ai/blob/main/docs/mdm-strategy.md"
            className="font-medium text-primary-600 hover:text-primary-700"
            target="_blank"
            rel="noopener noreferrer"
          >
            docs/mdm-strategy.md
          </Link>{' '}
          for the architecture.
        </p>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div>
        <Link
          href={`/dashboard/integrations/${providerSlug}`}
          className="text-xs font-medium text-gray-500 hover:text-gray-700"
        >
          ← {card.meta.displayName}
        </Link>
        <h1 className="mt-1 text-2xl font-bold text-gray-900">
          {card.meta.displayName} — Deployment profile
        </h1>
        <p className="mt-1 text-sm text-gray-600">
          Generate the right Configuration Profile for {card.meta.displayName}
          . Force-installs the Promptly browser extension on every
          managed device.
        </p>
      </div>

      {/* Inputs */}
      <div className="mt-6 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
        <h2 className="text-sm font-semibold text-gray-900">Profile inputs</h2>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Promptly extension ID
            </span>
            <input
              type="text"
              value={extensionId}
              onChange={(e) => setExtensionId(e.target.value)}
              placeholder={DEFAULT_EXTENSION_ID}
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 font-mono text-xs text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
            <p className="mt-1 text-[11px] text-gray-500">
              32-char Chrome Web Store ID. Replace the default once the
              extension is published.
            </p>
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Atlas tenant handle
            </span>
            <input
              type="text"
              value={tenantHandle}
              onChange={(e) => setTenantHandle(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
            <p className="mt-1 text-[11px] text-gray-500">
              Baked into the profile so the extension self-registers
              with the right atlas tenant on first launch.
            </p>
          </label>
          <label className="block sm:col-span-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Atlas API origin
            </span>
            <input
              type="text"
              value={atlasOrigin}
              onChange={(e) => setAtlasOrigin(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            />
          </label>
        </div>
      </div>

      {/* Install steps */}
      {bundle && (
        <div className="mt-6 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <h2 className="text-sm font-semibold text-gray-900">
            Install steps
          </h2>
          <StepList steps={bundle.installSteps} />
          {bundle.notes.length > 0 && (
            <div className="mt-4 rounded-md bg-amber-50 px-3 py-2 ring-1 ring-amber-100">
              <p className="text-xs font-semibold text-amber-900">Notes</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-amber-900">
                {bundle.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Generated files */}
      {bundle && (
        <div className="mt-6 space-y-4">
          {bundle.files.map((f) => (
            <FileBlock key={f.filename} file={f} onCopied={showToast} />
          ))}
        </div>
      )}

      <p className="mt-6 text-xs text-gray-500">
        Profiles regenerate live as you edit inputs above. Re-download
        whenever you change the extension ID or tenant handle. Atlas
        signs profiles with a tenant key once the Configuration
        Service ships in v0.4 — until then, profiles are unsigned (Jamf
        and Intune still accept unsigned configuration profiles in dev
        + production).
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
