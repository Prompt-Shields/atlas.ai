// Agent Conversations discovery data ported from the AI-SPM dashboard.
// This is faithful demo/sample data (no backend) used by the Discover page.

export type MessageRole = 'agent' | 'employee'

export interface Message {
  role: MessageRole
  text: string
  timestamp: string
  extracted?: string
}

export interface AgentConversation {
  id: string
  employeeId: string
  employeeName: string
  department: string
  channel: 'slack' | 'email' | 'web'
  status: 'in-progress' | 'complete' | 'no-response'
  messages: Message[]
  extractedUseCaseId: string | null
}

export const AGENT_CONVERSATIONS: AgentConversation[] = [
  {
    id: 'conv-legal',
    employeeId: 'sarah-chen',
    employeeName: 'Sarah Chen',
    department: 'Legal',
    channel: 'slack',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi Sarah — I\'m helping your security team understand how AI is being used across the organisation. Are you currently using any AI tools in your daily work?', timestamp: '2026-03-18T09:01:00Z' },
      { role: 'employee', text: 'Yes, we use ChatGPT for drafting contracts and summarising legal briefs.', timestamp: '2026-03-18T09:03:00Z' },
      { role: 'agent', text: 'Thanks! Does the contract text include any client-confidential information such as names, deal terms, or financial data?', timestamp: '2026-03-18T09:03:30Z' },
      { role: 'employee', text: 'Yes, client names and deal terms are included.', timestamp: '2026-03-18T09:05:00Z' },
      { role: 'agent', text: 'Got it. Who reviews the AI output before it\'s used — or does it go directly into documents?', timestamp: '2026-03-18T09:05:30Z' },
      { role: 'employee', text: 'I always review it, but junior associates sometimes use it directly.', timestamp: '2026-03-18T09:07:00Z', extracted: 'Use case extracted: Contract Review AI · GPT-4o · Confidential data · Owner: Sarah Chen' },
    ],
    extractedUseCaseId: 'contract-review',
  },
  {
    id: 'conv-it',
    employeeId: 'james-okafor',
    employeeName: 'James Okafor',
    department: 'IT',
    channel: 'slack',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi James — quick question from the security team. Is your engineering team using any AI coding assistants or automation tools?', timestamp: '2026-03-18T09:10:00Z' },
      { role: 'employee', text: 'Yes, GitHub Copilot for the whole team, and some people use Claude for code reviews.', timestamp: '2026-03-18T09:12:00Z' },
      { role: 'agent', text: 'Does Copilot have access to any proprietary internal codebases or API keys in the repo context?', timestamp: '2026-03-18T09:12:30Z' },
      { role: 'employee', text: 'It has access to our internal repos yes, including some that have config files.', timestamp: '2026-03-18T09:14:00Z', extracted: 'Use case extracted: Code Assistant · GitHub Copilot · Internal codebase · Owner: James Okafor' },
    ],
    extractedUseCaseId: 'code-assistant',
  },
  {
    id: 'conv-hr',
    employeeId: 'priya-sharma',
    employeeName: 'Priya Sharma',
    department: 'HR',
    channel: 'email',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi Priya, the security team is conducting an AI use case review. Does HR currently use any AI tools for recruitment or employee management?', timestamp: '2026-03-18T09:20:00Z' },
      { role: 'employee', text: 'Yes — we use an AI tool to screen CVs and rank candidates. Also using ChatGPT for writing job descriptions.', timestamp: '2026-03-18T09:45:00Z' },
      { role: 'agent', text: 'The CV screening tool — does it process any protected characteristics data like age, nationality, or gender from the CVs?', timestamp: '2026-03-18T09:46:00Z' },
      { role: 'employee', text: 'The CVs often include photos and dates of birth. We haven\'t configured it to ignore those.', timestamp: '2026-03-18T10:10:00Z', extracted: 'Use case extracted: CV Screening · AI Classifier · Sensitive personal data · Owner: Priya Sharma · HIGH RISK: Bias' },
    ],
    extractedUseCaseId: 'cv-screening',
  },
  {
    id: 'conv-shadow-1',
    employeeId: 'unowned-1',
    employeeName: 'Michael Torres',
    department: 'Finance',
    channel: 'web',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi Michael — I noticed ChatPDF was accessed from your machine using your corporate credentials. Are you using this for work purposes?', timestamp: '2026-03-18T10:00:00Z' },
      { role: 'employee', text: 'Oh yes, I use it to read client contracts and financial reports. It\'s much faster than reading the full PDFs.', timestamp: '2026-03-18T10:02:00Z' },
      { role: 'agent', text: 'Are those documents confidential? And was this tool approved by IT?', timestamp: '2026-03-18T10:02:30Z' },
      { role: 'employee', text: 'They\'re definitely confidential... I didn\'t know I needed approval. I just googled a tool that worked.', timestamp: '2026-03-18T10:04:00Z', extracted: '⚠️ Shadow AI detected: ChatPDF · Confidential contracts uploaded to unapproved external service · Owner: Michael Torres' },
    ],
    extractedUseCaseId: null,
  },
  {
    id: 'conv-claims',
    employeeId: 'emma-fischer',
    employeeName: 'Emma Fischer',
    department: 'Claims',
    channel: 'slack',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi Emma — I\'m mapping AI tools used in Claims. What AI systems does your team currently use in claims processing?', timestamp: '2026-03-18T10:30:00Z' },
      { role: 'employee', text: 'We have a fraud detection model, an AI for damage assessment from photos, and we auto-generate claims letters.', timestamp: '2026-03-18T10:32:00Z' },
      { role: 'agent', text: 'The fraud detection model — has it been tested for bias across customer demographics like age or ethnicity?', timestamp: '2026-03-18T10:32:30Z' },
      { role: 'employee', text: 'We had one audit last year but I\'m not sure the results were actioned. It\'s been flagged a few times.', timestamp: '2026-03-18T10:35:00Z', extracted: 'Use case extracted: Fraud Detection Model · Critical bias risk flagged · Owner: Emma Fischer' },
    ],
    extractedUseCaseId: 'fraud-detection',
  },
  {
    id: 'conv-finance',
    employeeId: 'marco-rossi',
    employeeName: 'Marco Rossi',
    department: 'Finance',
    channel: 'email',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi Marco — as CFO, I want to understand what AI tools Finance is using for reporting or forecasting. Can you help?', timestamp: '2026-03-18T11:00:00Z' },
      { role: 'employee', text: 'We use GPT-4 to help generate board reports and budget forecasts. I review everything before it goes out.', timestamp: '2026-03-18T11:30:00Z' },
      { role: 'agent', text: 'Are financial figures fed directly into the AI prompt, or does it only work with aggregated/anonymised data?', timestamp: '2026-03-18T11:31:00Z' },
      { role: 'employee', text: 'Full P&L data goes in. We haven\'t thought about whether that\'s a risk.', timestamp: '2026-03-18T11:45:00Z', extracted: 'Use case extracted: Report Generator · GPT-4o · Restricted financial data · Owner: Marco Rossi' },
    ],
    extractedUseCaseId: 'report-generator',
  },
  {
    id: 'conv-underwriting',
    employeeId: 'tom-bradley',
    employeeName: 'Tom Bradley',
    department: 'Underwriting',
    channel: 'slack',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi Tom — I\'m reviewing AI use in Underwriting. What AI systems are in use for risk assessment or pricing?', timestamp: '2026-03-18T11:00:00Z' },
      { role: 'employee', text: 'We have a risk assessment model and an LLM that explains pricing decisions to customers.', timestamp: '2026-03-18T11:05:00Z' },
      { role: 'agent', text: 'Does the risk assessment model use any customer demographic data like postcode or occupation that could create protected characteristic proxies?', timestamp: '2026-03-18T11:05:30Z' },
      { role: 'employee', text: 'Yes it uses postcode which I know can correlate with ethnicity. It\'s been flagged by our actuaries.', timestamp: '2026-03-18T11:08:00Z', extracted: 'Use case extracted: Policy Risk Assessor · Critical bias risk (postcode proxy) · Owner: Tom Bradley · EU AI Act gap' },
    ],
    extractedUseCaseId: 'policy-risk',
  },
  {
    id: 'conv-compliance',
    employeeId: 'lisa-wang',
    employeeName: 'Lisa Wang',
    department: 'Risk & Compliance',
    channel: 'slack',
    status: 'complete',
    messages: [
      { role: 'agent', text: 'Hi Lisa — as CRO, you\'re well placed to help. What AI tools does Risk & Compliance use, and are you aware of any ungoverned AI elsewhere?', timestamp: '2026-03-18T09:00:00Z' },
      { role: 'employee', text: 'We use AI for sanctions screening, regulatory monitoring, and risk scoring — all properly governed. But I know some teams are using ChatGPT informally.', timestamp: '2026-03-18T09:05:00Z' },
      { role: 'agent', text: 'Can you name any specific departments or tools you\'ve noticed being used informally?', timestamp: '2026-03-18T09:05:30Z' },
      { role: 'employee', text: 'Finance uses some PDF tool I don\'t recognise, and I heard HR is using an AI for CVs without proper vetting.', timestamp: '2026-03-18T09:08:00Z', extracted: 'Shadow AI leads: ChatPDF (Finance) · CV Screening tool (HR) · Referred for investigation' },
    ],
    extractedUseCaseId: 'risk-scoring',
  },
]

// SEEDED demo stats; can later derive from the AISPM use-case registry (see plan Task 1).
export const DISCOVERY_STATS = {
  toolsDiscovered: 38,
  vendorsInUse: 17,
  useCasesFound: 52,
  shadowAiFound: 12,
}
