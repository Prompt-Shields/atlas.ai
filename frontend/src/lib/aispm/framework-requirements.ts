// Framework requirements inventory — the cross-framework crosswalk.
//
// What each AI-governance framework says about the core governance topics
// (data, ownership, risk, oversight, …), at article/clause granularity. The
// point is "piggybacking": one governance topic maps to a clause in each
// framework, so a control evidenced once can satisfy GDPR, EU AI Act, NIST,
// ISO 42001 and EIOPA at the same time — instead of running five programmes.
//
// Reference data is illustrative but uses real article/clause numbers.

export type RequirementFrameworkKey =
  | 'gdpr'
  | 'euAiAct'
  | 'eiopa'
  | 'nistAiRmf'
  | 'iso42001'
  | 'owaspLlm'

export interface RequirementFramework {
  key: RequirementFrameworkKey
  label: string
  color: string
}

/** Display order leads with Gjensidige's priorities (privacy + insurance). */
export const REQUIREMENT_FRAMEWORKS: RequirementFramework[] = [
  { key: 'gdpr', label: 'GDPR', color: '#6366f1' },
  { key: 'eiopa', label: 'EIOPA', color: '#14b8a6' },
  { key: 'euAiAct', label: 'EU AI Act', color: '#22c55e' },
  { key: 'nistAiRmf', label: 'NIST AI RMF', color: '#0ea5e9' },
  { key: 'iso42001', label: 'ISO 42001', color: '#8b5cf6' },
  { key: 'owaspLlm', label: 'OWASP LLM', color: '#f59e0b' },
]

export interface FrameworkRef {
  fw: RequirementFrameworkKey
  /** Article / clause / control reference. */
  clause: string
  /** What that framework requires on this topic. */
  note: string
}

export interface RequirementTopic {
  id: string
  topic: string
  /** Plain-language statement of the cross-cutting requirement. */
  summary: string
  refs: FrameworkRef[]
}

export const REQUIREMENT_TOPICS: RequirementTopic[] = [
  {
    id: 'lawful-basis',
    topic: 'Lawful basis & purpose',
    summary:
      'Process personal data only on a valid legal basis, for a specified, legitimate purpose, and not beyond it.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 5(1)(b), 6', note: 'Purpose limitation; a lawful basis is required to process.' },
      { fw: 'euAiAct', clause: 'Art. 5', note: 'Prohibited AI practices set the outer bounds of permitted purpose.' },
      { fw: 'eiopa', clause: 'Proportionality', note: 'Use of AI must be proportionate to the business purpose.' },
      { fw: 'iso42001', clause: 'Cl. 6.1 · A.2', note: 'AI objectives and policy define and bound intended use.' },
      { fw: 'nistAiRmf', clause: 'GOVERN 1.1', note: 'Intended purpose and context are documented.' },
    ],
  },
  {
    id: 'data-minimisation',
    topic: 'Data minimisation & quality',
    summary:
      'Use only the data you need, keep it accurate and representative, and govern training/input data quality.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 5(1)(c)·(d)', note: 'Adequate, relevant, limited to what is necessary; kept accurate.' },
      { fw: 'euAiAct', clause: 'Art. 10', note: 'Data governance: relevant, representative, error-free training data.' },
      { fw: 'iso42001', clause: 'A.7', note: 'Data for AI systems — provenance, quality and preparation controls.' },
      { fw: 'nistAiRmf', clause: 'MAP 2.3 · MEASURE 2.x', note: 'Data quality and representativeness assessed.' },
      { fw: 'eiopa', clause: 'Data governance', note: 'Data accuracy, completeness and appropriateness.' },
    ],
  },
  {
    id: 'privacy-by-design',
    topic: 'Personal data & privacy by design',
    summary:
      'Build in data protection by design and default; protect special-category data; assess automated-decision impact.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 25, 9, 22', note: 'Privacy by design/default; special-category data; automated decisions.' },
      { fw: 'euAiAct', clause: 'Art. 10', note: 'Special-category data only under strict conditions for bias correction.' },
      { fw: 'iso42001', clause: 'A.7', note: 'Controls over personal and sensitive data used by AI.' },
      { fw: 'owaspLlm', clause: 'LLM06', note: 'Sensitive-information disclosure controls.' },
      { fw: 'eiopa', clause: 'Data governance', note: 'Protect policyholder personal data across the AI lifecycle.' },
    ],
  },
  {
    id: 'ownership',
    topic: 'Ownership & accountability',
    summary:
      'Name an accountable owner; define roles and responsibilities across the AI value chain.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 5(2), 24', note: 'Accountability principle; controller responsibility to demonstrate compliance.' },
      { fw: 'euAiAct', clause: 'Art. 16, 26', note: 'Provider and deployer obligations and responsibilities.' },
      { fw: 'iso42001', clause: 'Cl. 5.3 · A.3', note: 'Roles, responsibilities and authorities; internal organisation.' },
      { fw: 'nistAiRmf', clause: 'GOVERN 2.x', note: 'Roles and accountability structures are defined and resourced.' },
      { fw: 'eiopa', clause: 'Governance', note: 'Clear accountability; AI within the system of governance.' },
    ],
  },
  {
    id: 'risk-assessment',
    topic: 'Risk & impact assessment',
    summary:
      'Run a documented risk and impact assessment before and during deployment, sized to the risk.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 35', note: 'Data Protection Impact Assessment (DPIA) for high-risk processing.' },
      { fw: 'euAiAct', clause: 'Art. 9, 27', note: 'Risk-management system; Fundamental Rights Impact Assessment.' },
      { fw: 'iso42001', clause: 'Cl. 6.1 · A.5', note: 'AI risk assessment and AI system impact assessment.' },
      { fw: 'nistAiRmf', clause: 'MAP · MEASURE', note: 'Identify, analyse and measure risks across the lifecycle.' },
      { fw: 'eiopa', clause: 'Risk management', note: 'AI risks integrated into the risk-management system.' },
    ],
  },
  {
    id: 'human-oversight',
    topic: 'Human oversight',
    summary:
      'Keep a human able to understand, intervene in and override AI decisions that affect people.',
    refs: [
      { fw: 'euAiAct', clause: 'Art. 14', note: 'Effective human oversight of high-risk AI.' },
      { fw: 'gdpr', clause: 'Art. 22', note: 'Right not to be subject to solely automated decisions.' },
      { fw: 'iso42001', clause: 'A.9', note: 'Controls over the responsible use of AI systems.' },
      { fw: 'nistAiRmf', clause: 'MANAGE 2.x', note: 'Mechanisms to oversee, intervene and decommission.' },
      { fw: 'eiopa', clause: 'Human oversight', note: 'Human-in-command for material decisions.' },
    ],
  },
  {
    id: 'transparency',
    topic: 'Transparency & information',
    summary:
      'Tell people when AI is used and provide meaningful information about how it works and affects them.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 13–14', note: 'Information to data subjects, incl. logic of automated decisions.' },
      { fw: 'euAiAct', clause: 'Art. 13, 50', note: 'Transparency to deployers; disclosure of AI interaction.' },
      { fw: 'iso42001', clause: 'A.8', note: 'Information for interested parties.' },
      { fw: 'nistAiRmf', clause: 'GOVERN 4 · MEASURE', note: 'Transparency and explainability are documented.' },
      { fw: 'eiopa', clause: 'Transparency', note: 'Explainability appropriate to the audience.' },
    ],
  },
  {
    id: 'security',
    topic: 'Security & robustness',
    summary:
      'Protect AI systems and data against attack, misuse and failure; ensure accuracy and resilience.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 32', note: 'Security of processing appropriate to the risk.' },
      { fw: 'euAiAct', clause: 'Art. 15', note: 'Accuracy, robustness and cybersecurity.' },
      { fw: 'owaspLlm', clause: 'LLM01–LLM10', note: 'Prompt injection, data leakage and other LLM-specific risks.' },
      { fw: 'iso42001', clause: 'A.6', note: 'AI system lifecycle controls incl. security.' },
      { fw: 'nistAiRmf', clause: 'MANAGE 2.x', note: 'Resourced response to AI security incidents.' },
    ],
  },
  {
    id: 'records',
    topic: 'Record-keeping & documentation',
    summary:
      'Maintain documentation, logs and records sufficient to demonstrate compliance and trace decisions.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 30', note: 'Records of processing activities.' },
      { fw: 'euAiAct', clause: 'Art. 11, 12, 18', note: 'Technical documentation, automatic logging, record retention.' },
      { fw: 'iso42001', clause: 'Cl. 7.5', note: 'Documented information control.' },
      { fw: 'nistAiRmf', clause: 'GOVERN', note: 'Policies, processes and documentation maintained.' },
      { fw: 'eiopa', clause: 'Documentation', note: 'Audit trail proportionate to materiality.' },
    ],
  },
  {
    id: 'monitoring',
    topic: 'Monitoring & post-deployment',
    summary:
      'Monitor performance and drift in production; review and improve; report serious incidents.',
    refs: [
      { fw: 'euAiAct', clause: 'Art. 72', note: 'Post-market monitoring of high-risk AI.' },
      { fw: 'iso42001', clause: 'Cl. 9', note: 'Monitoring, internal audit and management review.' },
      { fw: 'nistAiRmf', clause: 'MEASURE · MANAGE', note: 'Ongoing monitoring of performance and risk.' },
      { fw: 'eiopa', clause: 'Monitoring', note: 'Continuous monitoring of AI outcomes.' },
      { fw: 'gdpr', clause: 'Art. 5(2)', note: 'Ongoing demonstration of compliance.' },
    ],
  },
  {
    id: 'third-party',
    topic: 'Third-party & vendor AI',
    summary:
      'Govern AI you obtain from vendors and processors; allocate responsibility along the value chain.',
    refs: [
      { fw: 'gdpr', clause: 'Art. 28', note: 'Processor obligations and data-processing agreements.' },
      { fw: 'euAiAct', clause: 'Art. 25', note: 'Responsibilities along the AI value chain.' },
      { fw: 'iso42001', clause: 'A.10', note: 'Third-party relationship controls.' },
      { fw: 'nistAiRmf', clause: 'GOVERN 6', note: 'Third-party risks and dependencies addressed.' },
      { fw: 'eiopa', clause: 'Outsourcing', note: 'Outsourcing and third-party AI under the governance system.' },
    ],
  },
]

export function frameworkLabel(key: RequirementFrameworkKey): RequirementFramework {
  const f = REQUIREMENT_FRAMEWORKS.find((x) => x.key === key)
  if (!f) throw new Error(`Unknown framework: ${key}`)
  return f
}
