// ─────────────────────────────────────────────────────────────────────
// MetaAgentBar — status summary strip for the agent discovery page.
//
// Four stat tiles (shadow/pending/approved/dismissed counts), coloured
// with atlas's emerald/amber/red/gray convention. Dumb presentational
// component — the page owns the counts.
// ─────────────────────────────────────────────────────────────────────

interface MetaAgentBarProps {
  shadow: number;
  pending: number;
  approved: number;
  dismissed: number;
}

export function MetaAgentBar({ shadow, pending, approved, dismissed }: MetaAgentBarProps) {
  const tiles = [
    { label: 'Shadow agents', value: shadow, tone: 'text-red-600' },
    { label: 'Pending review', value: pending, tone: 'text-amber-600' },
    { label: 'Approved', value: approved, tone: 'text-emerald-600' },
    { label: 'Dismissed', value: dismissed, tone: 'text-gray-500' },
  ];
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="text-sm text-gray-500">{t.label}</div>
          <div className={`text-2xl font-semibold ${t.tone}`}>{t.value}</div>
        </div>
      ))}
    </div>
  );
}
