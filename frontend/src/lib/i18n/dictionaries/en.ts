/**
 * English dictionary (M7, issue #251) — the canonical, fully-seeded locale.
 *
 * `fr.ts`/`nb.ts` implement the same `Dictionary` shape; see the scope note
 * on #251 for why only `nav`/`common`/`dashboardHome` are translated so far.
 */

const en = {
  common: {
    languageSwitcherLabel: 'Language',
    signOut: 'Sign Out',
  },
  nav: {
    dashboard: 'Dashboard',
    overview: 'Overview',
    tenants: 'Tenants',
    users: 'Users',
    teams: 'Teams',
    blobs: 'Blobs',
    risks: 'Risks',
    correlations: 'Correlations',
    activityLog: 'Activity Log',
    coaching: 'Coaching',
    handbook: 'Handbook',
    policies: 'Policies',
    registry: 'Registry',
    aiInventory: 'AI Inventory',
    saasVendorAi: 'SaaS Vendor AI',
    agentDiscovery: 'Agent Discovery',
    agentControl: 'Agent Control (Beta)',
    adoption: 'Adoption',
    reattestation: 'Re-attestation',
    map: 'Map',
    discover: 'Discover',
    register: 'Register',
    owners: 'Owners',
    modelRisk: 'Model Risk',
    auditReport: 'Audit & Report',
    comply: 'Comply',
    policyEnforcement: 'Policy Enforcement',
    piiShield: 'PII Shield',
    deployment: 'Deployment',
    endpoints: 'Endpoints',
    integrations: 'Integrations',
    aiSpend: 'AI Spend',
    developer: 'Developer',
    promptActivity: 'Prompt Activity',
    admin: 'Admin',
  },
  dashboardHome: {
    title: 'AI Governance Dashboard',
    welcome: 'Welcome back, {name}',
    defaultUserName: 'User',
    stats: {
      userActivity: 'User Activity',
      sourcesOfTruth: 'Sources of Truth',
      riskAnalyses: 'Risk Analyses',
      correlations: 'Correlations',
    },
    quickActions: {
      title: 'Quick Actions',
      ingestedData: 'Ingested Data',
      reviewRisks: 'Review Risks',
      governanceCorrelations: 'Governance Correlations',
      adminTools: 'Admin Tools',
    },
  },
};

export type Dictionary = typeof en;

export default en;
