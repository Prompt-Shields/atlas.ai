// ─────────────────────────────────────────────────────────────────────
// ChatGptReplica — the interactive PII Shield demo pane. A minimal chat
// UI: as the user types, /pii/detect runs live (debounced) to highlight
// PII and preview the placeholder mapping. On send, the prompt is
// anonymized via the backend, only the redacted text is "sent" to the
// (mocked) assistant, and the reply is re-identified before being shown
// to the user — the full detect → anonymize → send → re-identify
// round-trip runs through the real backend endpoints (mock is only the
// assistant reply itself; there is no live LLM call here).
// ─────────────────────────────────────────────────────────────────────
'use client';

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Send, ShieldAlert } from 'lucide-react';
import {
  anonymizePii,
  detectPii,
  previewPlaceholders,
  reidentifyPii,
  type PIISpan,
} from '@/lib/pii';
import { HighlightedText } from './highlighted-text';
import type { PiiStage } from './stage-card';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  spans: PIISpan[];
}

interface ChatGptReplicaProps {
  onStageStart: (stage: PiiStage) => void;
  onStageDone: (stage: PiiStage) => void;
  onPipelineReset: () => void;
  onPlaceholderMap: (map: Record<string, string>) => void;
}

/** Canned assistant reply, built from the redacted text the backend returned. */
function mockAssistantReply(redactedText: string): string {
  return `Got it — I've logged this for follow-up: "${redactedText}"`;
}

export function ChatGptReplica({
  onStageStart,
  onStageDone,
  onPipelineReset,
  onPlaceholderMap,
}: ChatGptReplicaProps) {
  const [input, setInput] = useState('');
  const [liveSpans, setLiveSpans] = useState<PIISpan[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Live PII preview while typing (debounced — no need to hit the
  // backend on every keystroke).
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!input.trim()) {
      setLiveSpans([]);
      return;
    }
    debounceRef.current = setTimeout(() => {
      detectPii(input)
        .then(res => setLiveSpans(res.spans))
        .catch(() => {
          /* best-effort live preview; a transient failure just skips a beat */
        });
    }, 400);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [input]);

  const livePreviewMap = previewPlaceholders(liveSpans);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;

    setSending(true);
    setError(null);
    onPipelineReset();
    setMessages(prev => [
      ...prev,
      { id: `u-${prev.length}`, role: 'user', text, spans: liveSpans },
    ]);
    setInput('');
    setLiveSpans([]);

    try {
      onStageStart('detect');
      onStageStart('anonymize');
      const anonymized = await anonymizePii(text);
      onStageDone('detect');
      onStageDone('anonymize');
      onPlaceholderMap(anonymized.placeholder_map);

      onStageStart('send');
      // Only `redacted_text` ever leaves this component — the assistant
      // never sees the original PII.
      const mockReplyRedacted = mockAssistantReply(anonymized.redacted_text);
      await new Promise(resolve => setTimeout(resolve, 400));
      onStageDone('send');

      onStageStart('reidentify');
      const reidentified = await reidentifyPii(mockReplyRedacted, anonymized.placeholder_map);
      onStageDone('reidentify');

      setMessages(prev => [
        ...prev,
        { id: `a-${prev.length}`, role: 'assistant', text: reidentified.text, spans: [] },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong talking to the PII service.');
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col h-[560px]">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
        <ShieldAlert size={15} className="text-indigo-500" aria-hidden="true" />
        <div className="text-sm font-semibold text-slate-800">Protected chat demo</div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-xs text-slate-400 italic py-6 text-center">
            Try a prompt with an email, phone number, SSN, or card number — it will be
            highlighted here and redacted before it ever reaches the assistant.
          </div>
        )}
        {messages.map(msg => (
          <div key={msg.id} className={`flex gap-2 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div
              aria-hidden="true"
              className={`flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold ${
                msg.role === 'assistant' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-600'
              }`}
            >
              {msg.role === 'assistant' ? 'A' : 'U'}
            </div>
            <div
              className={`text-xs px-3 py-2 rounded-xl max-w-md leading-relaxed ${
                msg.role === 'assistant'
                  ? 'bg-indigo-50 text-indigo-900 rounded-tl-none'
                  : 'bg-slate-100 text-slate-800 rounded-tr-none'
              }`}
            >
              <HighlightedText text={msg.text} spans={msg.spans} />
            </div>
          </div>
        ))}
        {sending && (
          <div className="text-[11px] text-slate-400 italic">Anonymizing, sending, re-identifying…</div>
        )}
        {error && (
          <div className="text-[11px] text-red-600 bg-red-50 border border-red-100 rounded-lg px-2.5 py-1.5">
            {error}
          </div>
        )}
      </div>

      <div className="border-t border-slate-100 p-3">
        {liveSpans.length > 0 && (
          <div className="mb-2 text-[11px] bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-1.5">
            <div className="text-amber-800 font-medium mb-1">
              PII detected — would redact to:
            </div>
            <div className="flex flex-wrap gap-1">
              {Object.keys(livePreviewMap).map(placeholder => (
                <code
                  key={placeholder}
                  className="font-mono bg-white border border-amber-200 rounded px-1.5 py-0.5 text-amber-700"
                >
                  {placeholder}
                </code>
              ))}
            </div>
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a prompt…"
            rows={2}
            className="flex-1 resize-none text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-200"
          />
          <button
            type="button"
            onClick={() => void handleSend()}
            disabled={!input.trim() || sending}
            className="flex-shrink-0 bg-slate-900 hover:bg-slate-800 disabled:bg-slate-300 text-white rounded-lg p-2.5"
            aria-label="Send"
          >
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
