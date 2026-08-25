'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { UserActivityBlobResponse, SourceOfTruthBlobResponse } from '@/lib/types';

type Tab = 'activity' | 'sources';

export default function BlobsPage() {
  const [tab, setTab] = useState<Tab>('activity');
  const [blobs, setBlobs] = useState<UserActivityBlobResponse[]>([]);
  const [sources, setSources] = useState<SourceOfTruthBlobResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);

  useEffect(() => {
    setPage(1);
  }, [tab]);

  useEffect(() => {
    if (tab === 'activity') {
      api.listUserActivityBlobs(page).then((data: any) => {
        setBlobs(data.blobs || []);
        setTotal(data.total || 0);
      }).catch(() => {});
    } else {
      api.listSourcesOfTruth(page).then((data: any) => {
        setSources(data.sources || []);
        setTotal(data.total || 0);
      }).catch(() => {});
    }
  }, [tab, page]);

  const statusBadge = (status: string) => {
    const colors = status === 'processed' ? 'bg-green-100 text-green-800'
      : status === 'pending' ? 'bg-gray-100 text-gray-600'
      : status === 'failed' ? 'bg-red-100 text-red-800'
      : 'bg-blue-100 text-blue-800';
    return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${colors}`}>{status}</span>;
  };

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Ingested Data</h1>
        <span className="text-sm text-gray-500">{total} total</span>
      </div>

      <div className="mt-4 flex border-b border-gray-200">
        <button
          onClick={() => setTab('activity')}
          className={`px-4 py-2 text-sm font-medium ${tab === 'activity' ? 'border-b-2 border-primary-600 text-primary-600' : 'text-gray-500 hover:text-gray-700'}`}
        >
          User Activity
        </button>
        <button
          onClick={() => setTab('sources')}
          className={`px-4 py-2 text-sm font-medium ${tab === 'sources' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
        >
          Sources of Truth
        </button>
      </div>

      <div className="mt-6 space-y-4">
        {tab === 'activity' && blobs.map(b => (
          <div key={b.id} className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-medium text-gray-900">{b.title || 'Untitled'}</h3>
                <p className="mt-1 text-sm text-gray-500">
                  Source: {b.source_type} | Adapter: {b.adapter_name} | Words: {b.word_count}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {b.is_test_data && (
                  <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">TEST</span>
                )}
                {statusBadge(b.processing_status)}
              </div>
            </div>
            <p className="mt-2 text-sm text-gray-600">{b.content_preview}...</p>
            <p className="mt-2 text-xs text-gray-400">{new Date(b.created_at).toLocaleString()}</p>
          </div>
        ))}

        {tab === 'sources' && sources.map(s => (
          <div key={s.id} className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-200">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-medium text-gray-900">{s.title}</h3>
                <p className="mt-1 text-sm text-gray-500">
                  Category: {s.source_category.replace(/_/g, ' ')} | Words: {s.word_count}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {s.is_test_data && (
                  <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800">TEST</span>
                )}
                {statusBadge(s.processing_status)}
              </div>
            </div>
            <p className="mt-2 text-sm text-gray-600">{s.content_preview}...</p>
            <p className="mt-2 text-xs text-gray-400">{new Date(s.created_at).toLocaleString()}</p>
          </div>
        ))}

        {((tab === 'activity' && blobs.length === 0) || (tab === 'sources' && sources.length === 0)) && (
          <div className="rounded-lg bg-white p-8 text-center text-gray-500 shadow-sm ring-1 ring-gray-200">
            {tab === 'activity'
              ? 'No user activity data ingested yet. Use the adapter endpoints to ingest data.'
              : 'No sources of truth submitted yet. Use the ingestion endpoint to submit policies and standards.'}
          </div>
        )}
      </div>

      {total > 20 && (
        <div className="mt-4 flex justify-center gap-2">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="rounded px-3 py-1 text-sm disabled:opacity-50">Previous</button>
          <span className="px-3 py-1 text-sm">Page {page}</span>
          <button onClick={() => setPage(p => p + 1)} disabled={(tab === 'activity' ? blobs.length : sources.length) < 20} className="rounded px-3 py-1 text-sm disabled:opacity-50">Next</button>
        </div>
      )}
    </div>
  );
}
