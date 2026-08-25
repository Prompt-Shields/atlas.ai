// ─────────────────────────────────────────────────────────────────────
// Use-case map — graph construction + force-directed layout (§3.5 /
// issue #250).
//
// atlas has no react-force-graph dependency (and no charting deps in
// general — see /dashboard/map's original SVG note). This module
// hand-rolls a small Fruchterman-Reingold force simulation instead of
// pulling in a graph library: repulsion between every node pair,
// attraction along edges, cooled over a fixed number of iterations.
// It runs once per graph (useMemo in the page) and returns static
// positions — cheap enough at the node counts this page ever renders
// (a tenant's use cases + their distinct tools/vendors/owners/risk
// tiers, capped by the /use-cases page_size below).
//
// `buildGraphFromUseCases` turns live UseCaseResponse rows into the
// same {nodes, edges} shape as the curated USE_CASE_MAP_NODES/EDGES
// fallback, so the page can render either without a branch in the
// SVG code. Edges always point from the more specific node to the
// more general one (use-case → tool, tool → vendor, use-case → owner,
// use-case → risk tier) — that direction is what lets the department/
// severity filters (lib consumer) find "everything reachable from a
// matching use case" with a plain forward walk.
// ─────────────────────────────────────────────────────────────────────

import type {
  UseCaseMapEdge,
  UseCaseMapNode,
  UseCaseRiskTier,
} from '@/lib/curated-demo-data';
import type { UseCaseResponse } from '@/lib/types';

const RISK_SERVER_TO_LABEL: Record<string, UseCaseRiskTier> = {
  LOW: 'Low',
  MEDIUM: 'Medium',
  HIGH: 'High',
};

// Heuristic tool → vendor mapping. The use-case registry only stores
// a freeform `tool` string (e.g. "ChatGPT", "Microsoft Copilot") —
// there's no vendor entity to join against, so this infers the vendor
// node the same way a person skimming the tool name would.
const TOOL_VENDOR_MAP: Record<string, string> = {
  ChatGPT: 'OpenAI',
  'GPT-4o': 'OpenAI',
  'GPT-4': 'OpenAI',
  'GPT-3.5': 'OpenAI',
  'GPT-3.5 Turbo': 'OpenAI',
  Claude: 'Anthropic',
  'Claude 3 Opus': 'Anthropic',
  'Claude 3.5 Sonnet': 'Anthropic',
  'Microsoft Copilot': 'Microsoft',
  Copilot: 'Microsoft',
  'Bing Chat': 'Microsoft',
  Gemini: 'Google',
  'Gemini 1.5 Pro': 'Google',
  Bard: 'Google',
};

export function inferVendor(tool: string): string {
  if (TOOL_VENDOR_MAP[tool]) return TOOL_VENDOR_MAP[tool];
  const lower = tool.toLowerCase();
  if (lower.includes('gpt') || lower.includes('openai') || lower.includes('chatgpt')) return 'OpenAI';
  if (lower.includes('claude') || lower.includes('anthropic')) return 'Anthropic';
  if (lower.includes('copilot') || lower.includes('azure')) return 'Microsoft';
  if (lower.includes('gemini') || lower.includes('bard') || lower.includes('google')) return 'Google';
  return 'Other';
}

/** Builds the same node/edge shape as the curated demo graph from live use-case rows. */
export function buildGraphFromUseCases(
  useCases: UseCaseResponse[],
): { nodes: UseCaseMapNode[]; edges: UseCaseMapEdge[] } {
  const nodes: UseCaseMapNode[] = [];
  const edges: UseCaseMapEdge[] = [];
  const seenTool = new Set<string>();
  const seenVendor = new Set<string>();
  const seenOwner = new Set<string>();
  const seenRisk = new Set<string>();

  for (const uc of useCases) {
    const riskTier = RISK_SERVER_TO_LABEL[uc.risk_tier] ?? 'Low';
    const ucId = `uc-${uc.id}`;
    nodes.push({
      id: ucId,
      label: uc.title,
      kind: 'use-case',
      department: uc.department,
      riskTier,
      meta: {
        tool: uc.tool,
        status: uc.status,
        // No owner→display-name join available on this endpoint yet
        // (see /dashboard/registry's serverToUseCase for the same
        // short-uuid convention).
        owner: uc.owner_user_id ? `…${uc.owner_user_id.slice(-8)}` : 'Unassigned',
      },
    });

    const toolId = `tool-${uc.tool}`;
    if (!seenTool.has(toolId)) {
      seenTool.add(toolId);
      nodes.push({ id: toolId, label: uc.tool, kind: 'model' });

      const vendor = inferVendor(uc.tool);
      const vendorId = `vendor-${vendor}`;
      if (!seenVendor.has(vendorId)) {
        seenVendor.add(vendorId);
        nodes.push({ id: vendorId, label: vendor, kind: 'vendor' });
      }
      edges.push({ from: toolId, to: vendorId, rel: 'vendor-of' });
    }
    edges.push({ from: ucId, to: toolId, rel: 'uses-model' });

    if (uc.owner_user_id) {
      const ownerId = `owner-${uc.owner_user_id}`;
      if (!seenOwner.has(ownerId)) {
        seenOwner.add(ownerId);
        nodes.push({ id: ownerId, label: `…${uc.owner_user_id.slice(-8)}`, kind: 'owner' });
      }
      edges.push({ from: ucId, to: ownerId, rel: 'owned-by' });
    }

    const riskId = `risk-${riskTier}`;
    if (!seenRisk.has(riskId)) {
      seenRisk.add(riskId);
      nodes.push({ id: riskId, label: `${riskTier} risk`, kind: 'risk' });
    }
    edges.push({ from: ucId, to: riskId, rel: 'has-risk' });
  }

  return { nodes, edges };
}

export interface PositionedMapNode extends UseCaseMapNode {
  x: number;
  y: number;
}

// Deterministic pseudo-random in [0, 1), seeded from a string id. Used
// instead of Math.random() so the initial layout is reproducible
// (same graph → same picture) and stable across server/client render
// passes for this 'use client' page.
function hashSeed(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function seededUnit(seed: number): number {
  const x = Math.sin(seed) * 43758.5453;
  return x - Math.floor(x);
}

/**
 * Hand-rolled Fruchterman-Reingold force-directed layout. Runs a fixed
 * number of cooled iterations of node repulsion + edge attraction and
 * returns final positions clamped to [margin, dimension - margin].
 */
export function forceDirectedLayout(
  nodes: UseCaseMapNode[],
  edges: UseCaseMapEdge[],
  width: number,
  height: number,
): PositionedMapNode[] {
  const margin = 48;
  const innerW = Math.max(1, width - margin * 2);
  const innerH = Math.max(1, height - margin * 2);
  const n = nodes.length;
  if (n === 0) return [];

  const posX = new Float64Array(n);
  const posY = new Float64Array(n);
  nodes.forEach((node, i) => {
    const seed = hashSeed(node.id);
    posX[i] = margin + seededUnit(seed) * innerW;
    posY[i] = margin + seededUnit(seed + 1) * innerH;
  });

  const indexById = new Map(nodes.map((node, i) => [node.id, i]));
  const edgeIdx: Array<{ a: number; b: number }> = [];
  for (const e of edges) {
    const a = indexById.get(e.from);
    const b = indexById.get(e.to);
    if (a !== undefined && b !== undefined && a !== b) edgeIdx.push({ a, b });
  }

  const dispX = new Float64Array(n);
  const dispY = new Float64Array(n);
  const area = innerW * innerH;
  const k = Math.sqrt(area / n) * 0.9;
  const iterations = n > 120 ? 120 : 220;
  let temperature = Math.max(innerW, innerH) / 10;

  for (let iter = 0; iter < iterations; iter++) {
    dispX.fill(0);
    dispY.fill(0);

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = posX[i] - posX[j];
        const dy = posY[i] - posY[j];
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const force = (k * k) / dist;
        const ux = dx / dist;
        const uy = dy / dist;
        dispX[i] += ux * force;
        dispY[i] += uy * force;
        dispX[j] -= ux * force;
        dispY[j] -= uy * force;
      }
    }

    for (const { a, b } of edgeIdx) {
      const dx = posX[a] - posX[b];
      const dy = posY[a] - posY[b];
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = (dist * dist) / k;
      const ux = dx / dist;
      const uy = dy / dist;
      dispX[a] -= ux * force;
      dispY[a] -= uy * force;
      dispX[b] += ux * force;
      dispY[b] += uy * force;
    }

    for (let i = 0; i < n; i++) {
      const dlen = Math.sqrt(dispX[i] * dispX[i] + dispY[i] * dispY[i]) || 0.01;
      const capped = Math.min(dlen, temperature);
      posX[i] = Math.min(width - margin, Math.max(margin, posX[i] + (dispX[i] / dlen) * capped));
      posY[i] = Math.min(height - margin, Math.max(margin, posY[i] + (dispY[i] / dlen) * capped));
    }

    temperature *= 0.95;
  }

  return nodes.map((node, i) => ({ ...node, x: posX[i], y: posY[i] }));
}

/** Adjacency list; `reverse` walks edges target→source instead of source→target. */
export function buildAdjacency(
  edges: UseCaseMapEdge[],
  reverse: boolean,
): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const e of edges) {
    const from = reverse ? e.to : e.from;
    const to = reverse ? e.from : e.to;
    const list = map.get(from);
    if (list) list.push(to);
    else map.set(from, [to]);
  }
  return map;
}

/** Breadth-first reachable set (inclusive of the start ids) over the given adjacency. */
export function bfsCollect(startIds: string[], adjacency: Map<string, string[]>): Set<string> {
  const visited = new Set(startIds);
  const queue = [...startIds];
  while (queue.length) {
    const id = queue.pop();
    if (id === undefined) break;
    for (const next of adjacency.get(id) ?? []) {
      if (!visited.has(next)) {
        visited.add(next);
        queue.push(next);
      }
    }
  }
  return visited;
}
