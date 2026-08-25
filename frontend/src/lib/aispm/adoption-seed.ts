// SEEDED demo data — atlas has no adoption-summary API
//
// The AISPM source (ai-spm-dashboard/app/adoption/page.tsx) is API-driven
// (/api/adoption/summary, /api/tour-engagement), which atlas.ai does not
// expose. The atlas Adoption page therefore renders this self-contained,
// badged "SAMPLE DATA" demo set instead of fetching anything.
//
// Self-contained on purpose: types live alongside the data, no imports.

export interface AdoptionOverview {
  dau: number;
  mau: number;
  totalPrompts: number;
  windowDays: 30;
}

export interface TopTool {
  tool: string;
  vendor: string;
  activeUsers: number;
  prompts: number;
}

export interface RiskActionRow {
  tool: string;
  allowed: number;
  redacted: number;
  blocked: number;
}

export const ADOPTION_OVERVIEW: AdoptionOverview = {
  dau: 412,
  mau: 1184,
  totalPrompts: 86420,
  windowDays: 30,
};

// Vendor-centric: which tools employees actually reach for, by vendor.
// Sorted at render time by activeUsers desc.
export const TOP_TOOLS: readonly TopTool[] = [
  { tool: 'ChatGPT', vendor: 'OpenAI', activeUsers: 742, prompts: 38210 },
  { tool: 'Claude', vendor: 'Anthropic', activeUsers: 531, prompts: 21940 },
  { tool: 'GitHub Copilot', vendor: 'GitHub', activeUsers: 318, prompts: 12760 },
  { tool: 'Gemini', vendor: 'Google', activeUsers: 264, prompts: 6480 },
  { tool: 'Microsoft Copilot', vendor: 'Microsoft', activeUsers: 197, prompts: 3920 },
  { tool: 'Perplexity', vendor: 'Perplexity AI', activeUsers: 142, prompts: 2110 },
  { tool: 'Midjourney', vendor: 'Midjourney', activeUsers: 88, prompts: 760 },
  { tool: 'Cursor', vendor: 'Anysphere', activeUsers: 61, prompts: 240 },
];

// Per-tool mix of policy outcomes over the window.
export const RISK_ACTION_BREAKDOWN: readonly RiskActionRow[] = [
  { tool: 'ChatGPT', allowed: 34120, redacted: 3210, blocked: 880 },
  { tool: 'Claude', allowed: 20680, redacted: 1040, blocked: 220 },
  { tool: 'GitHub Copilot', allowed: 12410, redacted: 280, blocked: 70 },
  { tool: 'Gemini', allowed: 6120, redacted: 290, blocked: 70 },
  { tool: 'Microsoft Copilot', allowed: 3760, redacted: 120, blocked: 40 },
];
