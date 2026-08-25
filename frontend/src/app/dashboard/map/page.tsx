'use client';

// ─────────────────────────────────────────────────────────────────────
// Use-Case Map — atlas's signature visualisation (§3.5 / issue #250)
//
// Force-directed graph of use cases → models/tools → vendors, plus
// owners and risk-tier nodes. Nodes are laid out client-side with a
// hand-rolled Fruchterman-Reingold simulation (lib/aispm/use-case-map)
// — atlas has no react-force-graph or other charting dependency, so
// this hand-rolls the same physics rather than adding one.
//
// Data: tries GET /use-cases live and builds the graph from those
// rows; on any failure (no backend, unauthenticated) it falls back to
// the curated USE_CASE_MAP_NODES/EDGES demo graph, same pattern as
// /dashboard/registry's serverUseCases state.
//
// Department + severity filters dim (not remove) non-matching nodes
// so the layout never jumps — "matching" means either a use-case node
// whose department/riskTier passes, or any node reachable by walking
// forward from such a use case (its tool, that tool's vendor, its
// owner, its risk-tier node). Clicking a node opens a detail drawer
// (pattern shared with AgentDrawer) summarising it and, for non-use-
// case nodes, the use cases connected to it.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import {
  USE_CASE_MAP_EDGES,
  USE_CASE_MAP_NODES,
  type UseCaseMapEdge,
  type UseCaseMapNode,
  type UseCaseMapNodeKind,
} from '@/lib/curated-demo-data';
import {
  bfsCollect,
  buildAdjacency,
  buildGraphFromUseCases,
  forceDirectedLayout,
  type PositionedMapNode,
} from '@/lib/aispm/use-case-map';

const VIEW_W = 1100;
const VIEW_H = 600;

const NODE_COLOURS: Record<UseCaseMapNodeKind, string> = {
  'use-case': '#3b82f6',
  model: '#8b5cf6',
  vendor: '#f97316',
  owner: '#10b981',
  risk: '#ef4444',
};

const KIND_LABELS: Record<UseCaseMapNodeKind, string> = {
  'use-case': 'Use case',
  model: 'Model / tool',
  vendor: 'Vendor',
  owner: 'Owner',
  risk: 'Risk',
};

const NODE_RADIUS = 8;

const selectCls =
  'rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500';

export default function UseCaseMapPage() {
  const [hovered, setHovered] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [department, setDepartment] = useState('all');
  const [severity, setSeverity] = useState('all');

  // Live data — null until loaded; null = use curated fallback so the
  // page still renders a graph without a backend (see registry page).
  const [serverGraph, setServerGraph] = useState<
    { nodes: UseCaseMapNode[]; edges: UseCaseMapEdge[] } | null
  >(null);
  const [liveError, setLiveError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.listUseCases({ page_size: 100 });
        if (!cancelled) {
          setServerGraph(buildGraphFromUseCases(res.use_cases));
        }
      } catch {
        if (!cancelled) setLiveError(true);
        /* keep curated fallback */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const graph = useMemo(
    () => serverGraph ?? { nodes: USE_CASE_MAP_NODES, edges: USE_CASE_MAP_EDGES },
    [serverGraph],
  );

  const positioned = useMemo(
    () => forceDirectedLayout(graph.nodes, graph.edges, VIEW_W, VIEW_H),
    [graph],
  );

  const nodeById = useMemo(
    () => new Map(positioned.map((n) => [n.id, n])),
    [positioned],
  );

  const forwardAdjacency = useMemo(() => buildAdjacency(graph.edges, false), [graph]);
  const reverseAdjacency = useMemo(() => buildAdjacency(graph.edges, true), [graph]);

  const departments = useMemo(() => {
    const set = new Set<string>();
    for (const n of graph.nodes) {
      if (n.kind === 'use-case' && n.department) set.add(n.department);
    }
    return [...set].sort();
  }, [graph]);

  // Nodes reachable (forward) from every use-case node that passes the
  // department/severity filters — this is what stays at full opacity.
  const activeIds = useMemo(() => {
    const matchingUseCaseIds = graph.nodes
      .filter(
        (n) =>
          n.kind === 'use-case' &&
          (department === 'all' || n.department === department) &&
          (severity === 'all' || n.riskTier === severity),
      )
      .map((n) => n.id);
    return bfsCollect(matchingUseCaseIds, forwardAdjacency);
  }, [graph, department, severity, forwardAdjacency]);

  const highlightedEdgeSet = useMemo(() => {
    if (!hovered) return null;
    const ids = new Set<number>();
    graph.edges.forEach((e, i) => {
      if (e.from === hovered || e.to === hovered) ids.add(i);
    });
    return ids;
  }, [hovered, graph]);

  const highlightedNodeIds = useMemo(() => {
    if (!hovered) return null;
    const ids = new Set<string>([hovered]);
    for (const e of graph.edges) {
      if (e.from === hovered) ids.add(e.to);
      if (e.to === hovered) ids.add(e.from);
    }
    return ids;
  }, [hovered, graph]);

  const selected = selectedId ? nodeById.get(selectedId) ?? null : null;
  const selectedUseCases = useMemo(() => {
    if (!selected || selected.kind === 'use-case') return [];
    const reachable = bfsCollect([selected.id], reverseAdjacency);
    return graph.nodes.filter((n) => n.kind === 'use-case' && reachable.has(n.id));
  }, [selected, reverseAdjacency, graph]);

  function opacityFor(id: string) {
    if (!activeIds.has(id)) return 0.12;
    if (highlightedNodeIds && !highlightedNodeIds.has(id)) return 0.25;
    return 1;
  }

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Use-case map</h1>
        <p className="mt-1 text-sm text-gray-600">
          Force-directed view of how AI use cases connect to models,
          vendors, owners, and risk tiers. Hover a node to highlight its
          edges, click one to open its detail panel.
        </p>
        {liveError && (
          <p className="mt-1 text-xs text-amber-600">
            Couldn&apos;t reach the use-case registry — showing demo data.
          </p>
        )}
      </div>

      {/* Filters */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <select
          value={department}
          onChange={(e) => setDepartment(e.target.value)}
          className={selectCls}
          aria-label="Filter by department"
        >
          <option value="all">All departments</option>
          {departments.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          className={selectCls}
          aria-label="Filter by risk severity"
        >
          <option value="all">All risk levels</option>
          <option value="Low">Low risk</option>
          <option value="Medium">Medium risk</option>
          <option value="High">High risk</option>
        </select>
        {(department !== 'all' || severity !== 'all') && (
          <button
            type="button"
            onClick={() => {
              setDepartment('all');
              setSeverity('all');
            }}
            className="text-sm font-medium text-primary-600 hover:text-primary-700"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-3 text-xs">
        {(Object.keys(KIND_LABELS) as UseCaseMapNodeKind[]).map((kind) => (
          <span key={kind} className="flex items-center gap-1.5">
            <span
              className="inline-block h-3 w-3 rounded-full"
              style={{ backgroundColor: NODE_COLOURS[kind] }}
              aria-hidden="true"
            />
            <span className="text-gray-700">{KIND_LABELS[kind]}</span>
          </span>
        ))}
      </div>

      {/* SVG canvas */}
      <div className="mt-4 overflow-x-auto rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-200">
        <svg
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          className="h-[480px] w-full"
          aria-label="AI use-case map"
        >
          {/* Edges */}
          {graph.edges.map((e, i) => {
            const from = nodeById.get(e.from);
            const to = nodeById.get(e.to);
            if (!from || !to) return null;
            const dimmed =
              !activeIds.has(from.id) ||
              !activeIds.has(to.id) ||
              (highlightedEdgeSet !== null && !highlightedEdgeSet.has(i));
            const midX = (from.x + to.x) / 2;
            const path = `M ${from.x} ${from.y} Q ${midX} ${from.y} ${midX} ${(from.y + to.y) / 2} T ${to.x} ${to.y}`;
            return (
              <path
                key={i}
                d={path}
                fill="none"
                stroke={dimmed ? '#e5e7eb' : '#9ca3af'}
                strokeWidth={dimmed ? 1 : 1.5}
              />
            );
          })}

          {/* Nodes */}
          {positioned.map((n) => (
            <g
              key={n.id}
              onMouseEnter={() => setHovered(n.id)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => setSelectedId(n.id)}
              style={{ cursor: 'pointer' }}
            >
              <circle
                cx={n.x}
                cy={n.y}
                r={n.id === selectedId ? NODE_RADIUS + 2 : NODE_RADIUS}
                fill={NODE_COLOURS[n.kind]}
                opacity={opacityFor(n.id)}
                stroke={n.id === selectedId ? '#111827' : '#fff'}
                strokeWidth={n.id === selectedId ? 2.5 : 2}
              />
              <text
                x={n.x}
                y={n.y - NODE_RADIUS - 4}
                textAnchor="middle"
                fontSize={11}
                fill={opacityFor(n.id) > 0.5 ? '#111827' : '#9ca3af'}
              >
                {n.label}
              </text>
            </g>
          ))}
        </svg>
      </div>

      {selected && (
        <NodeDetailPanel
          node={selected}
          connectedUseCases={selectedUseCases}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}

function NodeDetailPanel({
  node,
  connectedUseCases,
  onClose,
}: {
  node: PositionedMapNode;
  connectedUseCases: UseCaseMapNode[];
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-gray-900/40"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <span
              className="inline-block rounded-full px-2 py-0.5 text-[11px] font-medium text-white"
              style={{ backgroundColor: NODE_COLOURS[node.kind] }}
            >
              {KIND_LABELS[node.kind]}
            </span>
            <h2 className="mt-1.5 text-base font-semibold text-gray-900">{node.label}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        {node.kind === 'use-case' && (
          <section className="mt-5 space-y-3 text-sm">
            <DetailRow label="Department" value={node.department ?? '—'} />
            <DetailRow label="Tool" value={node.meta?.tool ?? '—'} />
            <DetailRow label="Status" value={node.meta?.status ?? '—'} />
            <DetailRow label="Risk tier" value={node.riskTier ?? '—'} />
            <DetailRow label="Owner" value={node.meta?.owner ?? 'Unassigned'} />
          </section>
        )}

        {node.kind !== 'use-case' && (
          <section className="mt-5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Connected use cases ({connectedUseCases.length})
            </h3>
            {connectedUseCases.length === 0 ? (
              <p className="mt-2 text-sm text-gray-500">No connected use cases.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {connectedUseCases.map((uc) => (
                  <li key={uc.id} className="rounded-lg border border-gray-200 p-2.5">
                    <p className="text-sm font-medium text-gray-900">{uc.label}</p>
                    <p className="mt-0.5 text-xs text-gray-500">
                      {uc.department ?? '—'} · {uc.riskTier ?? 'Low'} risk
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-gray-100 pb-2">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium text-gray-900">{value}</span>
    </div>
  );
}
