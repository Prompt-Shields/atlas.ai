'use client';

// ─────────────────────────────────────────────────────────────────────
// Endpoint compliance — cross-MDM device + extension view
//
// Implements §4.4 of docs/mdm-strategy.md. Aggregates managed-device
// data across every connected MDM (Intune today; Jamf / Kandji /
// JumpCloud in v0.2) plus the Promptly extension's device-register
// heartbeat to surface the gap atlas was built to close:
//
//   N devices managed  ×  M have the extension installed
//                       ×  O fully compliant
//
// Per-MDM enrolment summary at the top, detail table below.
//
// Data: ENDPOINT_MDM_AGGREGATES + ENDPOINT_DEVICES from
// curated-demo-data.ts. When the per-MDM sync workers land, swap
// to api.listEndpointCompliance() + api.listIntuneDevices() etc.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import {
  ENDPOINT_DEVICES,
  ENDPOINT_MDM_AGGREGATES,
  ORG,
  type EndpointDevice,
  type EndpointMdmAggregate,
} from '@/lib/curated-demo-data';
import type {
  EndpointDeviceServerResponse,
  EndpointMdmAggregateResponse,
  IntegrationProvider,
} from '@/lib/types';

// ─── Server → curated adapters ───────────────────────────────────────
//
// Same soft-fail pattern as other dashboard pages: if the live API
// returns data, use it; otherwise fall back to the curated demo set.

function serverToAggregate(
  r: EndpointMdmAggregateResponse,
): EndpointMdmAggregate {
  return {
    mdmProvider: r.mdm_provider,
    displayName: r.display_name,
    vendor: r.vendor,
    enrolledCount: r.enrolled_count,
    compliantCount: r.compliant_count,
    extensionInstalledCount: r.extension_installed_count,
    lastSyncedAt: r.last_synced_at,
  };
}

function serverToDevice(r: EndpointDeviceServerResponse): EndpointDevice {
  // Map server-side compliance strings to the typed UI union.
  const cs = r.compliance_state ?? 'unknown';
  const compliance: EndpointDevice['complianceState'] =
    cs === 'compliant' ||
    cs === 'noncompliant' ||
    cs === 'inGracePeriod' ||
    cs === 'unknown'
      ? cs
      : 'unknown';
  return {
    id: r.id,
    mdmProvider: r.mdm_provider,
    deviceName: r.device_name,
    operatingSystem: r.operating_system ?? 'Unknown',
    osVersion: r.os_version ?? '',
    enrolledUserEmail: r.enrolled_user_email ?? '',
    complianceState: compliance,
    extensionInstalled: r.extension_installed,
    lastSeenAt: r.last_seen_at ?? new Date().toISOString(),
  };
}

const COMPLIANCE_CLASSES: Record<
  EndpointDevice['complianceState'],
  string
> = {
  compliant: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  noncompliant: 'bg-red-50 text-red-700 ring-red-200',
  inGracePeriod: 'bg-amber-50 text-amber-700 ring-amber-200',
  unknown: 'bg-gray-100 text-gray-700 ring-gray-200',
};

const COMPLIANCE_LABELS: Record<EndpointDevice['complianceState'], string> = {
  compliant: 'Compliant',
  noncompliant: 'Non-compliant',
  inGracePeriod: 'In grace period',
  unknown: 'Unknown',
};

function ComplianceChip({
  state,
}: {
  state: EndpointDevice['complianceState'];
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${COMPLIANCE_CLASSES[state]}`}
    >
      {COMPLIANCE_LABELS[state]}
    </span>
  );
}

function VendorBadge({
  vendor,
  name,
}: {
  vendor: EndpointMdmAggregate['vendor'];
  name: string;
}) {
  const bg = vendor === 'microsoft' ? 'bg-blue-600' : 'bg-gray-700';
  return (
    <div className="flex items-center gap-2">
      <span
        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded font-bold text-[11px] text-white ${bg}`}
      >
        {name.charAt(0)}
      </span>
      <span className="text-sm font-medium text-gray-900">{name}</span>
    </div>
  );
}

function MdmAggregateCard({
  agg,
  onClick,
  selected,
}: {
  agg: EndpointMdmAggregate;
  onClick: () => void;
  selected: boolean;
}) {
  const compliancePct =
    agg.enrolledCount > 0
      ? Math.round((agg.compliantCount / agg.enrolledCount) * 100)
      : 0;
  const extensionPct =
    agg.enrolledCount > 0
      ? Math.round((agg.extensionInstalledCount / agg.enrolledCount) * 100)
      : 0;
  const isEmpty = agg.enrolledCount === 0;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isEmpty}
      className={
        'flex flex-col rounded-xl bg-white p-5 text-left shadow-sm transition ring-1 ' +
        (isEmpty
          ? 'cursor-not-allowed opacity-60 ring-gray-200'
          : selected
            ? 'ring-2 ring-primary-500'
            : 'ring-gray-200 hover:ring-primary-300')
      }
    >
      <div className="flex items-start justify-between">
        <VendorBadge vendor={agg.vendor} name={agg.displayName} />
        {isEmpty && (
          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700 ring-1 ring-blue-200">
            Not connected
          </span>
        )}
      </div>

      <div className="mt-4 text-3xl font-bold text-gray-900 tabular-nums">
        {agg.enrolledCount}
      </div>
      <p className="text-[11px] text-gray-500">enrolled devices</p>

      <div className="mt-4 space-y-2">
        <div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-700">Compliant</span>
            <span className="font-medium text-gray-900">
              {agg.compliantCount} ({compliancePct}%)
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-gray-100">
            <div
              className="h-1.5 rounded-full bg-emerald-500"
              style={{ width: `${compliancePct}%` }}
            />
          </div>
        </div>
        <div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-700">Extension installed</span>
            <span className="font-medium text-gray-900">
              {agg.extensionInstalledCount} ({extensionPct}%)
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-gray-100">
            <div
              className="h-1.5 rounded-full bg-primary-500"
              style={{ width: `${extensionPct}%` }}
            />
          </div>
        </div>
      </div>

      {agg.lastSyncedAt ? (
        <p className="mt-3 text-[11px] text-gray-500">
          Last sync{' '}
          {new Date(agg.lastSyncedAt).toLocaleString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </p>
      ) : (
        <Link
          href={`/dashboard/integrations`}
          className="mt-3 text-[11px] font-medium text-primary-600 hover:text-primary-700"
        >
          Connect {agg.displayName} →
        </Link>
      )}
    </button>
  );
}

export default function EndpointsPage() {
  const [selected, setSelected] = useState<string>('MICROSOFT_INTUNE');

  // Live state — null until first fetch; null = use curated fallback
  // so the demo flow keeps working without a backend.
  const [serverAggregates, setServerAggregates] = useState<
    EndpointMdmAggregate[] | null
  >(null);
  const [serverDevices, setServerDevices] = useState<EndpointDevice[] | null>(
    null,
  );

  // Fetch aggregates on mount. Soft-fail to curated.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.listEndpointCompliance();
        if (!cancelled) {
          setServerAggregates(res.aggregates.map(serverToAggregate));
        }
      } catch {
        /* keep curated fallback */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch devices whenever the selected MDM card changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.listEndpointDevices(
          selected as IntegrationProvider,
        );
        if (!cancelled) {
          setServerDevices(res.devices.map(serverToDevice));
        }
      } catch {
        if (!cancelled) {
          // Reset on error so we fall back to curated for this MDM.
          setServerDevices(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const aggregates = serverAggregates ?? ENDPOINT_MDM_AGGREGATES;

  const totals = useMemo(() => {
    let enrolled = 0;
    let compliant = 0;
    let installed = 0;
    for (const a of aggregates) {
      enrolled += a.enrolledCount;
      compliant += a.compliantCount;
      installed += a.extensionInstalledCount;
    }
    return { enrolled, compliant, installed };
  }, [aggregates]);

  const filteredDevices = useMemo(() => {
    if (serverDevices) return serverDevices;
    return ENDPOINT_DEVICES.filter((d) => d.mdmProvider === selected);
  }, [serverDevices, selected]);

  const extensionGap = totals.enrolled - totals.installed;
  const compliantPct =
    totals.enrolled > 0
      ? Math.round((totals.compliant / totals.enrolled) * 100)
      : 0;

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          Endpoint compliance
        </h1>
        <p className="mt-1 text-sm text-gray-600">
          {ORG.name} — managed devices × Promptly extension installed ×
          MDM compliance state. The Promptly extension is the
          observation layer; MDM is the deployment layer (see{' '}
          <Link
            href="https://github.com/jun-bit-pulse-ai/atlas.ai/blob/main/docs/mdm-strategy.md"
            className="font-medium text-primary-600 hover:text-primary-700"
            target="_blank"
            rel="noopener noreferrer"
          >
            MDM strategy
          </Link>
          ).
        </p>
      </div>

      {/* Hero tiles — cross-MDM totals */}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Devices managed
          </p>
          <p className="mt-2 text-3xl font-bold text-gray-900 tabular-nums">
            {totals.enrolled}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            MDM-compliant
          </p>
          <p className="mt-2 text-3xl font-bold text-emerald-600 tabular-nums">
            {totals.compliant}
            <span className="ml-1 text-base font-medium text-gray-500">
              ({compliantPct}%)
            </span>
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Extension installed
          </p>
          <p className="mt-2 text-3xl font-bold text-primary-600 tabular-nums">
            {totals.installed}
          </p>
        </div>
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Missing extension
          </p>
          <p className="mt-2 text-3xl font-bold text-amber-600 tabular-nums">
            {extensionGap}
          </p>
        </div>
      </div>

      {/* Per-MDM aggregate cards */}
      <h2 className="mt-8 text-sm font-semibold text-gray-900">
        Per-MDM enrolment
      </h2>
      <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {aggregates.map((a) => (
          <MdmAggregateCard
            key={a.mdmProvider}
            agg={a}
            onClick={() => setSelected(a.mdmProvider)}
            selected={selected === a.mdmProvider}
          />
        ))}
      </div>

      {/* Device detail table */}
      <h2 className="mt-8 text-sm font-semibold text-gray-900">
        Devices ·{' '}
        <span className="text-gray-600">
          {aggregates.find((a) => a.mdmProvider === selected)?.displayName ||
            '—'}
        </span>
      </h2>
      <div className="mt-3 overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-200">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-100 text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                <th className="px-4 py-2">Device</th>
                <th className="px-4 py-2">User</th>
                <th className="px-4 py-2">OS</th>
                <th className="px-4 py-2">Compliance</th>
                <th className="px-4 py-2">Extension</th>
                <th className="px-4 py-2">Last seen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filteredDevices.map((d) => (
                <tr key={d.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">
                      {d.deviceName}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-700">
                    {d.enrolledUserEmail}
                  </td>
                  <td className="px-4 py-3 text-gray-700">
                    {d.operatingSystem}
                    <span className="ml-1 text-xs text-gray-500">
                      {d.osVersion}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <ComplianceChip state={d.complianceState} />
                  </td>
                  <td className="px-4 py-3">
                    {d.extensionInstalled ? (
                      <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200">
                        ✓ Installed
                      </span>
                    ) : (
                      <span className="rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700 ring-1 ring-red-200">
                        ✗ Missing
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                    {new Date(d.lastSeenAt).toLocaleString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                </tr>
              ))}
              {filteredDevices.length === 0 && (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No devices synced from this MDM yet.{' '}
                    <Link
                      href="/dashboard/integrations"
                      className="font-medium text-primary-600 hover:text-primary-700"
                    >
                      Connect it →
                    </Link>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <p className="mt-4 text-xs text-gray-500">
        Extension-missing devices are the gap atlas was built to close.
        Push the Promptly extension via your MDM&apos;s Configuration
        Profile — recipes at{' '}
        <Link
          href="https://github.com/jun-bit-pulse-ai/atlas.ai/blob/main/docs/mdm-strategy.md#33-deployment-recipes-per-mdm"
          className="font-medium text-primary-600 hover:text-primary-700"
          target="_blank"
          rel="noopener noreferrer"
        >
          docs/mdm-strategy.md §3.3
        </Link>
        .
      </p>
    </div>
  );
}
