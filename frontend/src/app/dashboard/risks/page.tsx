'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { RiskAnalysisResponse } from '@/lib/types';

const severityColors: Record<string, string> = {
  critical: 'bg-red-100 text-red-800',
  high: 'bg-orange-100 text-orange-800',
  medium: 'bg-yellow-100 text-yellow-800',
  low: 'bg-blue-100 text-blue-800',
  informational: 'bg-gray-100 text-gray-600',
};

export default function RisksPage() {
  const [risks, setRisks] = useState<RiskAnalysisResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    api.listRisks(1).then((data: any) => {
      setRisks(data.risks || []);
      setTotal(data.total || 0);
    }).catch(() => {});
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Risk Assessments</h1>
        <span className="text-sm text-gray-500">{total} total</span>
      </div>

      <div className="mt-6 space-y-4">
        {risks.map(r => (
          <div key={r.id} className="rounded-lg bg-white shadow-sm ring-1 ring-gray-200">
            <div className="cursor-pointer p-4" onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <h3 className="font-medium text-gray-900">{r.risk_title}</h3>
                  <p className="mt-1 text-sm text-gray-500">{r.risk_category} | Score: {r.risk_score} | Confidence: {(r.confidence_score * 100).toFixed(0)}%</p>
                </div>
                <div className="flex items-center gap-2">
                  {r.is_test_data && <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">TEST</span>}
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${severityColors[r.risk_severity] || severityColors.informational}`}>
                    {r.risk_severity}
                  </span>
                </div>
              </div>
            </div>
            {expanded === r.id && (
              <div className="border-t border-gray-100 p-4 text-sm">
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <h4 className="font-semibold text-gray-900">Risk Description</h4>
                    <p className="mt-1 text-gray-600">{r.risk_description}</p>
                  </div>
                  <div>
                    <h4 className="font-semibold text-gray-900">Mitigation: {r.mitigation_title}</h4>
                    <p className="mt-1 text-gray-600">{r.mitigation_description}</p>
                    <ul className="mt-2 list-inside list-disc text-gray-600">
                      {r.mitigation_steps.map((step, i) => <li key={i}>{step}</li>)}
                    </ul>
                  </div>
                </div>
                {r.citations.length > 0 && (
                  <div className="mt-4">
                    <h4 className="font-semibold text-gray-900">Citations</h4>
                    {r.citations.map((c, i) => (
                      <blockquote key={i} className="mt-1 border-l-2 border-primary-300 pl-3 text-xs italic text-gray-500">
                        &quot;{c.snippet}&quot; <span className="text-primary-600">(source: {c.blob_id?.slice(0, 8)}...)</span>
                      </blockquote>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {risks.length === 0 && (
          <div className="rounded-lg bg-white p-8 text-center text-gray-500 shadow-sm ring-1 ring-gray-200">
            No risk assessments yet. Ingest source of truth data and run the risk extraction pipeline.
          </div>
        )}
      </div>
    </div>
  );
}
