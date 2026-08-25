// ─────────────────────────────────────────────────────────────────────
// KpiStrip — Policy Enforcement KPI tiles: Strict / Loose / Coverage /
// Blocks (30d) / Promotions ready. Dumb display component, caller
// computes the numbers.
// ─────────────────────────────────────────────────────────────────────

interface KpiStripProps {
  strict: number;
  loose: number;
  coveragePct: number;
  blocks30d: number;
  promotionsReady: number;
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white p-3 text-center shadow-sm ring-1 ring-gray-200">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 text-xl font-bold text-gray-900 tabular-nums">{value}</p>
    </div>
  );
}

export function KpiStrip({ strict, loose, coveragePct, blocks30d, promotionsReady }: KpiStripProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      <Tile label="Strict" value={String(strict)} />
      <Tile label="Loose" value={String(loose)} />
      <Tile label="Coverage" value={`${Math.round(coveragePct)}%`} />
      <Tile label="Blocks (30d)" value={String(blocks30d)} />
      <Tile label="Promotions ready" value={String(promotionsReady)} />
    </div>
  );
}
