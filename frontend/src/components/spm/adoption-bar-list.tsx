// ─────────────────────────────────────────────────────────────────────
// AdoptionBarList — shared horizontal bar list, 0–100% domain.
//
// First consumer: /dashboard/overview — "Adoption by department"
// Second consumer: /dashboard/coaching — "Active AI users by department"
//
// Promoted from inline-on-the-page to a shared component once /coaching
// landed. See docs/overview-page.md "promoting them to
// frontend/src/components/spm/ happens when the second consumer shows
// up" — this is that moment.
//
// The component is intentionally dumb:
//   - takes a {name, percent} array (already in display order if the
//     caller wants control; otherwise we sort descending)
//   - colours bars amber when ≤ laggingThreshold, emerald otherwise
//   - no titles, no card chrome — the page wraps it
//
// Rationale: every page that uses this wants a slightly different
// header / subhead / card wrapper. Keeping the chrome at the page
// level avoids forcing every consumer through the same layout.
// ─────────────────────────────────────────────────────────────────────

export interface AdoptionRow {
  name: string;
  percent: number; // 0–100
}

interface AdoptionBarListProps {
  rows: readonly AdoptionRow[];
  /**
   * Pre-sort the rows by percent descending. Default `true`. Pass
   * `false` if the caller wants to render in a custom order (e.g.,
   * alphabetical, or to keep the curated insertion order).
   */
  sort?: boolean;
  /**
   * Bars at or below this percent get an amber tint; everything else
   * gets emerald. Default `60`.
   */
  laggingThreshold?: number;
}

export function AdoptionBarList({
  rows,
  sort = true,
  laggingThreshold = 60,
}: AdoptionBarListProps) {
  const ordered = sort ? [...rows].sort((a, b) => b.percent - a.percent) : rows;
  return (
    <ul className="space-y-2">
      {ordered.map((row) => {
        const lagging = row.percent <= laggingThreshold;
        const barColor = lagging ? 'bg-amber-500' : 'bg-emerald-500';
        return (
          <li key={row.name} className="grid grid-cols-[1fr_auto] gap-x-3">
            <div>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs text-gray-700">
                  {row.name}
                </span>
                <span className="text-xs font-medium text-gray-900">
                  {row.percent}%
                </span>
              </div>
              <div className="mt-1 h-2 w-full rounded-full bg-gray-100">
                <div
                  className={`h-2 rounded-full ${barColor}`}
                  style={{ width: `${row.percent}%` }}
                />
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
