'use client';

/**
 * Voice-interview intake API client + scripted transcript (#242).
 *
 * Mirrors `app/schemas/voice_interview.py` and the
 * `/api/v1/discover/voice-interview` router: the frontend plays back
 * `SCRIPTED_INTERVIEW` (a canned agent/employee transcript, same demo
 * convention as `AGENT_CONVERSATIONS` in `lib/aispm/discovery.ts`), then
 * submits it for extraction — the backend's deterministic keyword
 * extractor (`services/voice_interview_service.py`) turns each detected
 * tool mention into a pending `UseCase` draft.
 */

import { api } from '../api';
import type { UseCaseResponse } from '../types';

// ──────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────

export type VoiceInterviewRole = 'agent' | 'employee';

export interface VoiceInterviewTurn {
  speaker: string;
  role: VoiceInterviewRole;
  text: string;
  department?: string;
}

export interface VoiceInterviewExtractRequest {
  transcript: VoiceInterviewTurn[];
  default_department?: string;
}

export interface VoiceInterviewExtractResponse {
  drafts: UseCaseResponse[];
  created_count: number;
}

// ──────────────────────────────────────────────────────────────────────
// Scripted transcript (playback demo data)
// ──────────────────────────────────────────────────────────────────────

export const SCRIPTED_INTERVIEW_DEPARTMENT = 'Grants Management';

export const SCRIPTED_INTERVIEW: VoiceInterviewTurn[] = [
  {
    speaker: 'Agent',
    role: 'agent',
    text: "Hi Priya — I'm interviewing a few teams about the AI tools they rely on day to day. Do you use any AI tools in Grants Management?",
  },
  {
    speaker: 'Priya Shah',
    role: 'employee',
    text: 'Yes — I use ChatGPT to draft grant renewal memos and summarise applicant proposals.',
    department: SCRIPTED_INTERVIEW_DEPARTMENT,
  },
  {
    speaker: 'Agent',
    role: 'agent',
    text: 'Good to know. Anything you use for engineering or reporting work specifically?',
  },
  {
    speaker: 'Priya Shah',
    role: 'employee',
    text: "We've also started using GitHub Copilot for our internal reporting scripts — it's saved the team a lot of time.",
    department: SCRIPTED_INTERVIEW_DEPARTMENT,
  },
  {
    speaker: 'Agent',
    role: 'agent',
    text: 'Understood — anything else worth flagging before we wrap up?',
  },
  {
    speaker: 'Priya Shah',
    role: 'employee',
    text: "That's about it for now, nothing else comes to mind.",
    department: SCRIPTED_INTERVIEW_DEPARTMENT,
  },
];

// ──────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────

const BASE = '/discover/voice-interview';

export const voiceInterviewApi = {
  extract: (payload: VoiceInterviewExtractRequest): Promise<VoiceInterviewExtractResponse> =>
    api.request<VoiceInterviewExtractResponse>(`${BASE}/extract`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};
