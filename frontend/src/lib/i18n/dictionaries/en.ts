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
    pilotReport: 'Pilot Report',
    workClient: 'Work Client',
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
  workClient: {
    title: 'Work client',
    subtitle: 'The free Promptly Guide Work client and its 90-day rule.',
    loading: 'Loading…',
    loadError: 'Could not load the Work client status: {error}',
    saveError: 'Could not save: {error}',
    status: {
      heading: 'Status',
      notStarted: 'Not started',
      notStartedBody: 'Day 0 has not been recorded, so the 90 days have not begun.',
      counting: 'Day {day} of {total}',
      daysLeft: '{days} days left, until {date}.',
      daysLeftOne: '1 day left, until {date}.',
      warning: 'Day 90 is close',
      warningBody:
        'Connect your own endpoint or add a paid module before {date} to keep the free client.',
      endpointRequired: 'Own endpoint required',
      endpointRequiredBody:
        'The 90 days ended on {date}, and there is no paid module and no declared endpoint.',
      exemptPaid: 'Kept: a paid module is active',
      exemptPaidBody: 'A paid module keeps the free client whatever the day. Today is day {day}.',
      exemptOwn: 'Kept: own endpoint connected',
      exemptOwnBody:
        'Your declared endpoint keeps the free client whatever the day. Today is day {day}.',
    },
    start: {
      button: 'Record today as day 0',
      note: 'Do this when your organisation starts using the Work client. Day 0 cannot be moved or cleared afterwards, so the 90 days cannot be restarted.',
      confirm: 'Record today as day 0 of the Work client? This cannot be undone.',
    },
    connected: {
      heading: 'What “connected” means',
      body: 'Connected means an admin has declared here that Guide answers from your organisation’s own model endpoint. Atlas never sees the endpoint or its key, so it cannot check this: it records your declaration.',
      declared: 'Declared: Guide answers from our own model endpoint.',
      notDeclared: 'Not declared.',
      declare: 'Declare our own endpoint',
      withdraw: 'Withdraw the declaration',
      withdrawConfirm:
        'Withdraw the declaration? Without a paid module, the 90-day rule applies again.',
    },
    howTo: {
      heading: 'How to connect your own endpoint',
      step1: 'On each Mac, open Guide’s Settings and go to “Cloud model (optional)”.',
      step2: 'Choose your organisation’s provider: Azure OpenAI, Amazon Bedrock, Anthropic or an OpenAI-compatible endpoint. Enter its endpoint, model and data region.',
      step3: 'Enter the key. It stays in that Mac’s Keychain and never reaches Atlas.',
      step4: 'Come back here and declare your own endpoint.',
      note: 'A configuration profile can turn the cloud model off, but never on, so it is turned on in Guide on each Mac.',
    },
    dayNinety: {
      heading: 'What happens at day 90',
      rule: 'If there is no paid module and no declared endpoint by day 90, Atlas marks the Work client “Own endpoint required”. A paid module or a declaration makes it active again at any time.',
      today: 'Nothing is switched off today. Promptly does not host a model yet: Guide answers on the Mac, or from an endpoint your organisation sets up, and both keep working.',
    },
  },
};

export type Dictionary = typeof en;

export default en;
