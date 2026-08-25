'use client';

// ─────────────────────────────────────────────────────────────────────
// Discover · MCP servers — /dashboard/discover/mcp  (M3, issue #241)
//
// Sub-page of Discover: collectors (IDE config scans, network traffic,
// browser extension telemetry, host process lists) emit observations that
// the backend correlates and scores into MCP server candidates. Promote
// pushes a candidate into the AI inventory as an AIAsset. Wired to
// /api/v1/discover/mcp via lib/aispm/mcp-discovery.ts.
// ─────────────────────────────────────────────────────────────────────

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  mcpDiscoveryApi,
  type DiscoveredMcpServer,
} from '@/lib/aispm/mcp-discovery';
import { McpDiscoveryList } from '@/components/spm/mcp-discovery';

export default function McpDiscoveryPage() {
  const [servers, setServers] = useState<DiscoveredMcpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setServers((await mcpDiscoveryApi.list()).servers);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load discovered MCP servers');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runScan = async () => {
    setScanning(true);
    setError(null);
    try {
      await mcpDiscoveryApi.scan();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const promote = async (server: DiscoveredMcpServer) => {
    setBusyId(server.id);
    setError(null);
    try {
      await mcpDiscoveryApi.promote(server.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to promote server');
    } finally {
      setBusyId(null);
    }
  };

  const dismiss = async (server: DiscoveredMcpServer) => {
    setBusyId(server.id);
    setError(null);
    try {
      await mcpDiscoveryApi.dismiss(server.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to dismiss server');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Link href="/dashboard/discover" className="text-sm text-indigo-600 hover:underline">
            ← Discover
          </Link>
          <h1 className="mt-1 text-3xl font-semibold">MCP Server Discovery</h1>
          <p className="text-gray-600">
            Candidates fused from IDE configs, network traffic, browser telemetry, and process lists.
          </p>
        </div>
        <button
          className="rounded bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
          disabled={scanning}
          onClick={runScan}
        >
          {scanning ? 'Scanning…' : 'Run scan'}
        </button>
      </div>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {loading ? (
        <div className="py-12 text-center text-gray-500">Loading discovered MCP servers…</div>
      ) : servers.length === 0 ? (
        <div className="py-12 text-center text-gray-500">
          No MCP servers discovered yet. Run a scan to correlate the latest collector observations.
        </div>
      ) : (
        <McpDiscoveryList
          servers={servers}
          busyId={busyId}
          onPromote={(server) => void promote(server)}
          onDismiss={(server) => void dismiss(server)}
        />
      )}
    </div>
  );
}
