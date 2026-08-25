'use client';

// ─────────────────────────────────────────────────────────────────────
// Discover · Voice-interview intake — /dashboard/discover/voice-interview
// (M3, issue #242)
//
// Plays back a scripted agent/employee interview transcript, then submits
// it to the backend for extraction: `POST /discover/voice-interview/extract`
// runs a deterministic keyword extractor over the employee turns and
// persists one pending (DRAFT) UseCase per detected AI-tool mention —
// see lib/aispm/voice-interview.ts and services/voice_interview_service.py.
// ─────────────────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  SCRIPTED_INTERVIEW,
  SCRIPTED_INTERVIEW_DEPARTMENT,
  voiceInterviewApi,
  type VoiceInterviewTurn,
} from '@/lib/aispm/voice-interview';
import type { UseCaseResponse } from '@/lib/types';

function TranscriptTurn({ turn }: { turn: VoiceInterviewTurn }) {
  const isAgent = turn.role === 'agent';
  return (
    <div className={`flex gap-2 ${isAgent ? '' : 'flex-row-reverse'}`}>
      <div
        aria-hidden="true"
        className={`flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
          isAgent ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'
        }`}
      >
        {isAgent ? 'A' : turn.speaker[0]}
      </div>
      <div
        className={`max-w-md rounded-xl px-3 py-2 text-sm leading-relaxed ${
          isAgent
            ? 'rounded-tl-none bg-indigo-50 text-indigo-800'
            : 'rounded-tr-none bg-gray-100 text-gray-700'
        }`}
      >
        {turn.text}
      </div>
    </div>
  );
}

export default function VoiceInterviewPage() {
  const [visibleCount, setVisibleCount] = useState(0);
  const [extracting, setExtracting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<UseCaseResponse[] | null>(null);

  useEffect(() => {
    if (visibleCount >= SCRIPTED_INTERVIEW.length) return;
    const id = window.setTimeout(() => setVisibleCount((c) => c + 1), 1100);
    return () => window.clearTimeout(id);
  }, [visibleCount]);

  const playbackDone = visibleCount >= SCRIPTED_INTERVIEW.length;

  const handleExtract = async () => {
    setExtracting(true);
    setError(null);
    try {
      const result = await voiceInterviewApi.extract({
        transcript: SCRIPTED_INTERVIEW,
        default_department: SCRIPTED_INTERVIEW_DEPARTMENT,
      });
      setDrafts(result.drafts);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Extraction failed');
    } finally {
      setExtracting(false);
    }
  };

  const replay = () => {
    setDrafts(null);
    setError(null);
    setVisibleCount(0);
  };

  return (
    <div>
      <Link
        href="/dashboard/discover"
        className="text-sm font-medium text-primary-600 hover:text-primary-700"
      >
        ← Discover
      </Link>
      <h1 className="mt-1 text-2xl font-bold text-gray-900">
        Voice-interview intake
      </h1>
      <p className="mt-1 text-sm text-gray-600">
        An AI agent interviews an employee; the transcript is parsed into
        documented use-case drafts.
      </p>

      <div className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">
            Interview transcript
          </h2>
          {playbackDone && (
            <button
              type="button"
              onClick={replay}
              className="text-xs font-medium text-primary-600 hover:text-primary-700"
            >
              Replay
            </button>
          )}
        </div>
        <div className="mt-4 space-y-3">
          {SCRIPTED_INTERVIEW.slice(0, Math.max(visibleCount, 1)).map((turn, i) => (
            <TranscriptTurn key={i} turn={turn} />
          ))}
        </div>
      </div>

      <div className="mt-6 flex items-center gap-3">
        <button
          type="button"
          onClick={() => void handleExtract()}
          disabled={!playbackDone || extracting}
          className="rounded-md bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {extracting ? 'Extracting…' : 'Extract use cases'}
        </button>
        {!playbackDone && (
          <span className="text-xs text-gray-500">
            Waiting for the transcript to finish playing…
          </span>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {drafts && (
        <div className="mt-6 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-200">
          <h2 className="text-sm font-semibold text-gray-900">
            {drafts.length === 0
              ? 'No use cases detected in this transcript'
              : `${drafts.length} use-case draft${drafts.length === 1 ? '' : 's'} saved to the registry`}
          </h2>
          {drafts.length > 0 && (
            <ul className="mt-4 divide-y divide-gray-100">
              {drafts.map((d) => (
                <li key={d.id} className="flex items-center justify-between py-3">
                  <div>
                    <div className="text-sm font-medium text-gray-900">{d.title}</div>
                    <div className="text-xs text-gray-500">
                      {d.tool} · {d.department}
                    </div>
                  </div>
                  <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200">
                    Pending review
                  </span>
                </li>
              ))}
            </ul>
          )}
          {drafts.length > 0 && (
            <Link
              href="/dashboard/registry"
              className="mt-4 inline-block text-xs font-medium text-primary-600 hover:text-primary-700"
            >
              Review drafts in the Registry →
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
