'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { CorrelationResponse } from '@/lib/types';

export default function CorrelationsPage() {
  const [correlations, setCorrelations] = useState<CorrelationResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showExcluded, setShowExcluded] = useState(false);
  const [excludeModal, setExcludeModal] = useState<string | null>(null);
  const [excludeReason, setExcludeReason] = useState('');
  const [loading, setLoading] = useState(false);

  const loadCorrelations = () => {
    api.listCorrelations(1, showExcluded).then((data: any) => {
      setCorrelations(data.correlations || []);
      setTotal(data.total || 0);
    }).catch(() => {});
  };

  useEffect(() => { loadCorrelations(); }, [showExcluded]);

  const handleExclude = async (id: string) => {
    if (!excludeReason.trim()) return;
    setLoading(true);
    try {
      await api.excludeCorrelation(id, excludeReason);
      setExcludeModal(null);
      setExcludeReason('');
      loadCorrelations();
    } catch { /* handled by api client */ }
    setLoading(false);
  };

  const handleInclude = async (id: string) => {
    setLoading(true);
    try {
      await api.includeCorrelation(id);
      loadCorrelations();
    } catch { /* handled by api client */ }
    setLoading(false);
  };

  const priorityColors: Record<string, string> = {
    critical: 'bg-red-100 text-red-800',
    high: 'bg-orange-100 text-orange-800',
    medium: 'bg-yellow-100 text-yellow-800',
    low: 'bg-green-100 text-green-800',
  };

  const reliabilityColor = (r: number) => {
    if (r >= 0.8) return 'text-green-700';
    if (r >= 0.5) return 'text-yellow-700';
    return 'text-red-700';
  };

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Governance Correlations</h1>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={showExcluded}
              onChange={(e) => setShowExcluded(e.target.checked)}
              className="rounded border-gray-300"
            />
            Show excluded
          </label>
          <span className="text-sm text-gray-500">{total} total</span>
        </div>
      </div>

      <div className="mt-6 space-y-4">
        {correlations.map(c => (
          <div
            key={c.id}
            className={`rounded-lg bg-white shadow-sm ring-1 ${c.is_excluded ? 'ring-gray-300 opacity-60' : 'ring-gray-200'}`}
          >
            <div className="cursor-pointer p-4" onClick={() => setExpanded(expanded === c.id ? null : c.id)}>
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium text-gray-900">{c.correlation_title}</h3>
                    {c.is_excluded && (
                      <span className="rounded bg-gray-200 px-1.5 py-0.5 text-xs text-gray-600">Excluded</span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-gray-500">
                    Type: {c.correlation_type} | Score: {c.overall_risk_score} | Status: {c.status}
                  </p>
                  <div className="mt-1.5 flex items-center gap-3 text-xs">
                    <span className={`font-medium ${reliabilityColor(c.score_reliability)}`}>
                      Reliability: {(c.score_reliability * 100).toFixed(0)}%
                    </span>
                    <span className="text-gray-400">|</span>
                    <span className="text-gray-500">
                      Activities: {c.user_activity_blob_ids.length} | Risks: {c.risk_analysis_ids.length}
                    </span>
                  </div>
                  {c.match_tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {c.match_tags.map((tag, i) => (
                        <span key={i} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                          {tag.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="ml-4 flex flex-col items-end gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${priorityColors[c.priority] || 'bg-gray-100'}`}>
                    {c.priority}
                  </span>
                  {!c.is_excluded ? (
                    <button
                      onClick={(e) => { e.stopPropagation(); setExcludeModal(c.id); }}
                      className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                      disabled={loading}
                    >
                      Exclude
                    </button>
                  ) : (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleInclude(c.id); }}
                      className="rounded px-2 py-1 text-xs text-green-600 hover:bg-green-50"
                      disabled={loading}
                    >
                      Re-include
                    </button>
                  )}
                </div>
              </div>
            </div>

            {expanded === c.id && (
              <div className="border-t border-gray-100 p-4 text-sm">
                <p className="text-gray-600">{c.correlation_summary}</p>

                {c.is_excluded && c.exclusion_reason && (
                  <div className="mt-3 rounded bg-red-50 p-3">
                    <h5 className="text-xs font-medium text-red-800">Exclusion Reason</h5>
                    <p className="mt-1 text-xs text-red-700">{c.exclusion_reason}</p>
                  </div>
                )}

                <h4 className="mt-4 font-semibold">Action Plan: {c.action_plan_title}</h4>
                <p className="mt-1 text-gray-600">{c.action_plan_description}</p>

                {c.action_steps.length > 0 && (
                  <div className="mt-3">
                    <h5 className="font-medium">Steps:</h5>
                    <ol className="mt-1 list-inside list-decimal text-gray-600">
                      {c.action_steps.map((s, i) => (
                        <li key={i}>{s.step} <span className="text-xs text-gray-400">({s.priority}, {s.effort})</span></li>
                      ))}
                    </ol>
                  </div>
                )}

                {c.citations.length > 0 && (
                  <div className="mt-3">
                    <h5 className="font-medium">Citations:</h5>
                    <ul className="mt-1 space-y-1">
                      {c.citations.map((cit, i) => (
                        <li key={i} className="rounded bg-gray-50 p-2 text-xs">
                          <span className={`font-medium ${cit.source_type === 'user_activity' ? 'text-purple-700' : 'text-red-700'}`}>
                            [{cit.source_type === 'user_activity' ? 'Activity' : 'Risk'}]
                          </span>
                          {' '}<span className="text-gray-500">{cit.source_id?.slice(0, 8)}...</span>
                          {' '}<span className="text-gray-700">&ldquo;{cit.snippet}&rdquo;</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="mt-4 rounded bg-gray-50 p-3">
                  <h5 className="font-medium text-gray-700">Reasoning</h5>
                  <p className="mt-1 text-xs text-gray-600">{c.reasoning}</p>
                </div>
              </div>
            )}

            {excludeModal === c.id && (
              <div className="border-t border-gray-100 p-4">
                <h4 className="text-sm font-medium text-gray-900">Exclude this correlation</h4>
                <textarea
                  value={excludeReason}
                  onChange={(e) => setExcludeReason(e.target.value)}
                  placeholder="Reason for exclusion..."
                  className="mt-2 w-full rounded border border-gray-300 p-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  rows={3}
                />
                <div className="mt-2 flex justify-end gap-2">
                  <button
                    onClick={() => { setExcludeModal(null); setExcludeReason(''); }}
                    className="rounded px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => handleExclude(c.id)}
                    disabled={!excludeReason.trim() || loading}
                    className="rounded bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    Confirm Exclude
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        {correlations.length === 0 && (
          <div className="rounded-lg bg-white p-8 text-center text-gray-500 shadow-sm ring-1 ring-gray-200">
            No correlations yet. Run the pipeline to correlate user activities with extracted risks.
          </div>
        )}
      </div>
    </div>
  );
}
