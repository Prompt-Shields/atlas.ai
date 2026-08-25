// ─────────────────────────────────────────────────────────────────────
// Defender for Cloud Apps import — enriched candidate list (M3, issue #243).
//
// Renders the extracted/enriched candidates from `defenderImportApi.list()`:
// a risk bar, compliance-status chips, and Accept/Dismiss actions for
// candidates not yet decided. No Recharts/framer-motion — hand-rolled div
// bar, matching atlas's convention (`components/spm/mcp-discovery.tsx`).
// ─────────────────────────────────────────────────────────────────────

import type {
  DefenderImportStatus,
  DiscoveredDefenderApp,
} from '@/lib/aispm/defender-import';

const STATUS_LABELS: Record<DefenderImportStatus, string> = {
  pending: 'Pending review',
  accepted: 'Accepted',
  dismissed: 'Dismissed',
};

const STATUS_TONES: Record<DefenderImportStatus, string> = {
  pending: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  accepted: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  dismissed: 'bg-gray-100 text-gray-600 ring-gray-500/20',
};

const COMPLIANCE_TONES: Record<string, string> = {
  covered: 'bg-emerald-50 text-emerald-700',
  partial: 'bg-amber-50 text-amber-700',
  gap: 'bg-red-50 text-red-700',
};

function StatusBadge({ status }: { status: DefenderImportStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_TONES[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}

function RiskBar({ score }: { score: number }) {
  const colour = score >= 60 ? 'bg-red-500' : score >= 30 ? 'bg-amber-500' : 'bg-emerald-500';
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 rounded-full bg-gray-100">
        <div className={`h-2 rounded-full ${colour}`} style={{ width: `${Math.round(score)}%` }} />
      </div>
      <span className="font-medium text-gray-900 tabular-nums">{Math.round(score)}</span>
    </div>
  );
}

function ComplianceChips({ status }: { status: Record<string, string> }) {
  const entries = Object.entries(status);
  if (entries.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {entries.map(([framework, value]) => (
        <span
          key={framework}
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ${COMPLIANCE_TONES[value] ?? 'bg-gray-50 text-gray-600'}`}
        >
          {framework} · {value}
        </span>
      ))}
    </div>
  );
}

function formatVolume(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

interface DefenderAppCardProps {
  app: DiscoveredDefenderApp;
  busy?: boolean;
  onAccept: (app: DiscoveredDefenderApp) => void;
  onDismiss: (app: DiscoveredDefenderApp) => void;
}

export function DefenderAppCard({ app, busy, onAccept, onDismiss }: DefenderAppCardProps) {
  const decidable = app.status === 'pending';
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-gray-900">{app.name}</span>
          <StatusBadge status={app.status} />
        </div>
        <div className="mt-1 text-xs text-gray-500">
          {app.category.replace(/_/g, ' ')}
          {' · '}
          {app.users_count} user{app.users_count === 1 ? '' : 's'}
          {' · '}
          {formatVolume(app.data_volume_mb)} transferred
        </div>
        <ComplianceChips status={app.compliance_status} />
        <div className="mt-2 text-xs text-gray-400">
          Imported {new Date(app.imported_at).toLocaleString()}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-3">
        <RiskBar score={app.risk_score} />
        {decidable && (
          <div className="flex gap-2">
            <button
              className="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              disabled={busy}
              onClick={() => onDismiss(app)}
            >
              Dismiss
            </button>
            <button
              className="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
              disabled={busy}
              onClick={() => onAccept(app)}
            >
              Accept into inventory
            </button>
          </div>
        )}
        {app.status === 'accepted' && app.promoted_vendor_id && (
          <span className="text-xs text-emerald-600">In AI inventory</span>
        )}
      </div>
    </div>
  );
}

interface DefenderImportListProps {
  apps: DiscoveredDefenderApp[];
  busyId?: string | null;
  onAccept: (app: DiscoveredDefenderApp) => void;
  onDismiss: (app: DiscoveredDefenderApp) => void;
}

export function DefenderImportAppList({ apps, busyId, onAccept, onDismiss }: DefenderImportListProps) {
  return (
    <div className="space-y-3">
      {apps.map((app) => (
        <DefenderAppCard
          key={app.id}
          app={app}
          busy={busyId === app.id}
          onAccept={onAccept}
          onDismiss={onDismiss}
        />
      ))}
    </div>
  );
}
