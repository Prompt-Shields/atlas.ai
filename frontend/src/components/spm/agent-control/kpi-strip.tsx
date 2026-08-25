// ─────────────────────────────────────────────────────────────────────
// KpiStrip — top-of-page summary tiles for the agent control panel.
//
// Dumb presentational component — the page derives the counts from the
// loaded AgentControlState[] and hands them down.
// ─────────────────────────────────────────────────────────────────────

interface KpiStripProps {
  total: number;
  healthy: number;
  guardrailsOff: number;
  underAction: number;
}

export function KpiStrip({ total, healthy, guardrailsOff, underAction }: KpiStripProps) {
  const tiles = [
    { label: 'Governed agents', value: total, tone: 'text-gray-900' },
    { label: 'Healthy', value: healthy, tone: 'text-emerald-600' },
    { label: 'Guardrails off', value: guardrailsOff, tone: 'text-red-600' },
    { label: 'Paused / quarantined', value: underAction, tone: 'text-amber-600' },
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
