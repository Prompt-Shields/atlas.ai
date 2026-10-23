'use client';

/**
 * Promptly Guide's admin console in Atlas.AI (promptly-guide N1, option A).
 *
 * Mirrors app/schemas/guide_console.py. The settings are the console document of
 * Guide's file console (promptly-guide deploy/console.example.json), in the same
 * shape; the backend checks it exactly as the file console does and refuses, in the
 * file console's words, anything it would refuse. This module only edits the document
 * and fetches the files made from it: Atlas never changes a Mac itself.
 */

import { api } from './api';

/** The settings for every Mac, or for one group. Only restrictions and content. */
export interface ConsoleSection {
  cloudModelDisabled?: boolean;
  requireEUProcessing?: boolean;
  memoryOff?: boolean;
  /** Groups only: the Work client's 90-day notices on these Macs, an admin's. Mac only. */
  workClientNotices?: boolean;
  adoptionTeam?: string;
  excludedApps?: string[];
  excludedWindowsApps?: string[];
  excludedWindowTitles?: string[];
  excludedSites?: string[];
  skillPacksOff?: string[];
  packFolders?: string[];
}

export interface KnowledgeSource {
  id: string;
  name?: string;
  folder: string;
  enabled?: boolean;
  groups?: string[];
}

export interface SignIn {
  firebaseApiKey?: string;
  firebaseProjectId?: string;
  provider?: string;
  tenantId?: string;
  signInHost?: string;
  processingArea?: string;
  groupsClaim?: string;
  groupsValidForHours?: number;
}

export interface ConsoleDocument {
  organisation: string;
  identifier: string;
  everyone?: ConsoleSection;
  groups?: Record<string, ConsoleSection>;
  knowledgeSources?: KnowledgeSource[];
  signIn?: SignIn;
  atlas?: { url: string };
}

export interface ConsoleFile {
  name: string;
  group: string | null;
}

export interface GuideConsole {
  console: ConsoleDocument | null;
  revision: number;
  updated_at: string | null;
  updated_by: string | null;
  profiles: ConsoleFile[];
  windows_files: ConsoleFile[];
  windows_notes: string[];
  /** Why no files are offered: saved settings this version of Guide refuses. */
  problem: string | null;
}

/** The skill packs Guide ships (promptly-guide Scripts/console/make-profiles.py KNOWN_PACKS). */
export const KNOWN_PACKS = ['chatgpt', 'claude', 'claude-code', 'm365-copilot'] as const;

/** The list fields a section has, in the order the page shows them. */
export const LIST_FIELDS = [
  'excludedApps',
  'excludedWindowsApps',
  'excludedWindowTitles',
  'excludedSites',
  'packFolders',
] as const;
export type ListField = (typeof LIST_FIELDS)[number];

export const SIGN_IN_FIELDS = [
  'firebaseApiKey',
  'firebaseProjectId',
  'provider',
  'tenantId',
  'signInHost',
  'processingArea',
  'groupsClaim',
] as const;
export type SignInTextField = (typeof SIGN_IN_FIELDS)[number];

export function emptyDocument(organisation = ''): ConsoleDocument {
  return { organisation, identifier: '', everyone: {} };
}

/** One entry per line, as the page shows a list. */
export function toLines(values: string[] | undefined): string {
  return (values ?? []).join('\n');
}

/** The lines of a text box as a list: trimmed, blank lines dropped. */
export function fromLines(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/**
 * A copy of `section` with `field` set, or left out when empty or false. An empty list
 * or a false switch restricts nothing, and the file console writes nothing for either.
 */
export function withField<T extends object, K extends keyof T>(section: T, field: K, value: T[K]): T {
  const next = { ...section };
  const empty =
    value === undefined ||
    value === false ||
    (typeof value === 'string' && value.trim() === '') ||
    (Array.isArray(value) && value.length === 0);
  if (empty) delete next[field];
  else next[field] = value;
  return next;
}

/** What is sent: empty optional sections left out, as a hand-written console file would. */
export function cleaned(doc: ConsoleDocument): ConsoleDocument {
  const out: ConsoleDocument = { ...doc };
  if (out.groups && Object.keys(out.groups).length === 0) delete out.groups;
  if (out.knowledgeSources && out.knowledgeSources.length === 0) delete out.knowledgeSources;
  if (out.signIn && Object.keys(out.signIn).length === 0) delete out.signIn;
  if (out.atlas && !out.atlas.url.trim()) delete out.atlas;
  return out;
}

function saveBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export const guideConsoleApi = {
  get(): Promise<GuideConsole> {
    return api.request<GuideConsole>('/guide/console');
  },

  /** Tenant admins only. Refused with the file console's own words when it would refuse. */
  save(console: ConsoleDocument, note: string): Promise<GuideConsole> {
    return api.request<GuideConsole>('/guide/console', {
      method: 'PUT',
      body: JSON.stringify({ console: cleaned(console), note: note.trim() || null }),
    });
  },

  async downloadProfile(name: string): Promise<void> {
    saveBlob(await api.requestBlob(`/guide/console/profiles/${encodeURIComponent(name)}`), name);
  },

  async downloadProfiles(): Promise<void> {
    saveBlob(await api.requestBlob('/guide/console/profiles.zip'), 'promptly-guide-profiles.zip');
  },

  async downloadWindows(): Promise<void> {
    saveBlob(await api.requestBlob('/guide/console/windows.zip'), 'promptly-guide-windows.zip');
  },
};
