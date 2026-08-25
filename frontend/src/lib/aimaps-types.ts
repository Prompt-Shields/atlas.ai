export interface AIModel {
  id: string
  name: string
  provider: string
  type: 'llm' | 'classifier' | 'vision' | 'embedding'
}

export interface Mitigation {
  id: string
  name: string
  description: string
  status: 'applied' | 'pending' | 'not-applied'
  ownerId?: string
  dueDate?: string
}

export type ComplianceLevel = 'covered' | 'partial' | 'gap'

export interface ComplianceStatus {
  euAiAct: ComplianceLevel
  nistAiRmf: ComplianceLevel
  owaspLlm: ComplianceLevel
  iso42001: ComplianceLevel
}

export interface Risk {
  id: string
  name: string
  category: 'hallucination' | 'data-leakage' | 'prompt-injection' | 'bias' | 'ip-exposure' | 'compliance' | 'shadow-ai'
  severity: 'critical' | 'high' | 'medium' | 'low'
  owaspRef?: string
  nistRef?: string
  euAiActRef?: string
  mitigations: Mitigation[]
}

export interface Person {
  id: string
  name: string
  email: string
  department: string
  role: string
  useCaseIds: string[]
  assessmentsPending: number
  assessmentsComplete: number
}

export type UseCaseStatus = 'discovered' | 'assessed' | 'owned' | 'mitigated' | 'compliant'
export type DiscoveryMethod = 'agent-campaign' | 'self-register' | 'auto-detect' | 'shadow-ai'

export interface UseCase {
  id: string
  name: string
  description: string
  department: string
  models: AIModel[]
  ownerId: string | null
  dataClassification: 'public' | 'internal' | 'confidential' | 'restricted'
  risks: Risk[]
  mitigations: Mitigation[]
  complianceStatus: ComplianceStatus
  discoveryMethod: DiscoveryMethod
  status: UseCaseStatus
  createdAt: string
  lastReviewedAt: string
}

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
