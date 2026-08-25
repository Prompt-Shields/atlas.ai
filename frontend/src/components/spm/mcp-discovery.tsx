// ─────────────────────────────────────────────────────────────────────
// MCP server discovery — scored candidate list (M3, issue #241).
//
// Renders the correlated/scored candidates from `mcpDiscoveryApi.list()`:
// a confidence bar (source count drives it — see
// `mcp_discovery_service.score_server()`), the corroborating sources as
// chips, and a Promote action for candidates not yet in the AI inventory.
// No Recharts/framer-motion — hand-rolled div bar, matching atlas's
// convention (`components/spm/coverage-bar.tsx`).
// ─────────────────────────────────────────────────────────────────────

import type { DiscoveredMcpServer, McpDiscoveryStatus } from '@/lib/aispm/mcp-discovery';

const STATUS_LABELS: Record<McpDiscoveryStatus, string> = {
  candidate: 'Candidate',
  promoted: 'Promoted',
  dismissed: 'Dismissed',
};

const STATUS_TONES: Record<McpDiscoveryStatus, string> = {
  candidate: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  promoted: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  dismissed: 'bg-gray-100 text-gray-600 ring-gray-500/20',
};

function StatusBadge({ status }: { status: McpDiscoveryStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_TONES[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  );
}

function ConfidenceBar({ score }: { score: number }) {
  const colour = score >= 70 ? 'bg-emerald-500' : score >= 40 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 rounded-full bg-gray-100">
        <div className={`h-2 rounded-full ${colour}`} style={{ width: `${Math.round(score)}%` }} />
      </div>
      <span className="font-medium text-gray-900 tabular-nums">{Math.round(score)}</span>
    </div>
  );
}

function SourceChips({ sources }: { sources: string[] }) {
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {sources.map((source) => (
        <span
          key={source}
          className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600"
        >
          {source.replace(/_/g, ' ')}
        </span>
      ))}
    </div>
  );
}

interface McpServerCardProps {
  server: DiscoveredMcpServer;
  busy?: boolean;
  onPromote: (server: DiscoveredMcpServer) => void;
  onDismiss: (server: DiscoveredMcpServer) => void;
}

export function McpServerCard({ server, busy, onPromote, onDismiss }: McpServerCardProps) {
  const decidable = server.status === 'candidate';
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-gray-900">{server.name}</span>
          <StatusBadge status={server.status} />
        </div>
        <div className="mt-1 text-xs text-gray-500">
          {server.transport}
          {server.host && <> · {server.host}</>}
          {' · '}
          {server.observation_count} observation{server.observation_count === 1 ? '' : 's'}
        </div>
        {server.description && <p className="mt-2 text-sm text-gray-600">{server.description}</p>}
        <SourceChips sources={server.sources} />
        <div className="mt-2 text-xs text-gray-400">
          Last observed {new Date(server.last_observed_at).toLocaleString()}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-3">
        <ConfidenceBar score={server.confidence_score} />
        {decidable && (
          <div className="flex gap-2">
            <button
              className="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              disabled={busy}
              onClick={() => onDismiss(server)}
            >
              Dismiss
            </button>
            <button
              className="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
              disabled={busy}
              onClick={() => onPromote(server)}
            >
              Promote to inventory
            </button>
          </div>
        )}
        {server.status === 'promoted' && server.promoted_asset_id && (
          <span className="text-xs text-emerald-600">In AI inventory</span>
        )}
      </div>
    </div>
  );
}

interface McpDiscoveryListProps {
  servers: DiscoveredMcpServer[];
  busyId?: string | null;
  onPromote: (server: DiscoveredMcpServer) => void;
  onDismiss: (server: DiscoveredMcpServer) => void;
}

export function McpDiscoveryList({ servers, busyId, onPromote, onDismiss }: McpDiscoveryListProps) {
  return (
    <div className="space-y-3">
      {servers.map((server) => (
        <McpServerCard
          key={server.id}
          server={server}
          busy={busyId === server.id}
          onPromote={onPromote}
          onDismiss={onDismiss}
        />
      ))}
    </div>
  );
}
