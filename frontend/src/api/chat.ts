import { api } from './client'

export interface RoleProfile { name: string; scope: string; department_id: string | null }
export interface Me { id: string; name: string; email: string; status: string; roles: RoleProfile[] }

export interface Usage {
  configured: boolean
  enabled: boolean
  monthly_remaining: number | null
  daily_remaining: number | null
  company_budget_blocked: boolean | null
}

export interface Conversation {
  conversation_id: string
  mode: 'department' | 'orchestrator'
  department_ids: string[]
  created_at: string
}

export type SourceLocation =
  | { kind: 'pdf_page'; page_number: number }
  | { kind: 'slide'; slide_number: number }
  | { kind: 'sheet_range'; sheet_name: string; cell_range: string }
  | { kind: 'image'; image_number: number }
  | { kind: 'markdown_section' | 'reviewed_text'; section: string }

export interface Source {
  department_id: string
  document_id: number
  version_number: number
  name: string
  quote: string
  location: SourceLocation
}

export interface Answer {
  question_request_id: string
  conversation_id: string
  status: 'answered' | 'no_answer' | 'partial' | 'conflict'
  answer: string
  answer_kind: 'generated' | 'verbatim_excerpt' | 'system_notice'
  llm_called: boolean
  sources: Source[]
  unanswered_departments: string[]
  handoff_available: boolean
}

export interface StoredTurn {
  turn_id: string
  sequence: number
  created_at: string
  question: string | null
  response: Answer | null
  content_status: 'available' | 'access_revoked' | 'verification_unavailable'
}

export const getMe = () => api<Me>('/auth/me')
export const getUsage = () => api<Usage>('/me/usage')
export const getMyDepartmentName = () =>
  api<{ id: string; name: string }>('/me/department').catch(() => null)

export const listConversations = () =>
  api<{ items: Conversation[]; next_cursor: string | null }>('/api/conversations?limit=100')

export const createConversation = (departmentId: string, requestKey: string) =>
  api<Conversation>('/api/conversations', {
    method: 'POST',
    body: JSON.stringify({ request_key: requestKey, mode: 'department', department_ids: [departmentId] }),
  })

export const listTurns = (conversationId: string) =>
  api<{ items: StoredTurn[] }>(`/api/conversations/${conversationId}/messages?limit=100`)

export const ask = (p: { question: string; departmentId: string; conversationId: string; requestKey: string }) =>
  api<Answer>('/api/documents/ask', {
    method: 'POST',
    body: JSON.stringify({
      question: p.question,
      department_id: p.departmentId,
      conversation_id: p.conversationId,
      request_key: p.requestKey,
    }),
  })
