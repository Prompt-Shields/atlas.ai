// ─────────────────────────────────────────────────────────────────────
// CoverageBar — shared stacked covered/partial/gap bar.
//
// A single horizontal bar split into three segments — emerald (covered),
// amber (partial), red (gap) — sized by share of `total`. Ported from the
// inline coverage bar on the /comply overview (green/yellow/red in the
// source); recoloured to atlas's emerald/amber/red coverage palette.
//
// Like AdoptionBarList, this is intentionally dumb: no titles, no card
// chrome, no legend. The caller wraps it and supplies the surrounding
// label/stats row.
// ─────────────────────────────────────────────────────────────────────

interface CoverageBarProps {
  covered: number;
  partial: number;
  gap: number;
  total: number;
}

export function CoverageBar({ covered, partial, gap, total }: CoverageBarProps) {
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0);
  return (
    <div className="flex h-2 overflow-hidden rounded-full bg-slate-100">
      <div
        className="h-full bg-emerald-500 transition-all"
        style={{ width: `${pct(covered)}%` }}
      />
      <div
        className="h-full bg-amber-400 transition-all"
        style={{ width: `${pct(partial)}%` }}
      />
      <div
        className="h-full bg-red-400 transition-all"
        style={{ width: `${pct(gap)}%` }}
      />
    </div>
  );
}
