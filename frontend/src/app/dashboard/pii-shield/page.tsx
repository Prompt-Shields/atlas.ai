'use client';

// /dashboard/pii-shield — PII Shield (issue #246).
//
// Ported from ai-spm-dashboard/app/pii-shield/page.tsx. A ChatGPT-replica
// demo pane shows the detect → anonymize → send → re-identify pipeline
// running against the real backend (POST /api/v1/pii/*, issue #245):
// typing highlights PII live and previews the placeholder mapping;
// sending anonymizes the prompt, "sends" only the redacted text to a
// mocked assistant, then re-identifies the reply before it's shown.

import { useState } from 'react';
import { ChatGptReplica } from '@/components/spm/pii-shield/chatgpt-replica';
import { MappingPanel } from '@/components/spm/pii-shield/mapping-panel';
import { StageCard, type PiiStage } from '@/components/spm/pii-shield/stage-card';

export default function PiiShieldPage() {
  const [activeStage, setActiveStage] = useState<PiiStage | null>(null);
  const [completedStages, setCompletedStages] = useState<Set<PiiStage>>(new Set());
  const [placeholderMap, setPlaceholderMap] = useState<Record<string, string>>({});

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-bold text-slate-900">PII Shield</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          See PII detection, redaction and reversible re-identification run live before a
          prompt ever leaves the browser.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <ChatGptReplica
            onStageStart={stage => setActiveStage(stage)}
            onStageDone={stage => {
              setActiveStage(null);
              setCompletedStages(prev => new Set(prev).add(stage));
            }}
            onPipelineReset={() => setCompletedStages(new Set())}
            onPlaceholderMap={map => setPlaceholderMap(map)}
          />
        </div>
        <div className="space-y-4">
          <StageCard activeStage={activeStage} completedStages={completedStages} />
          <MappingPanel placeholderMap={placeholderMap} />
        </div>
      </div>
    </div>
  );
}
