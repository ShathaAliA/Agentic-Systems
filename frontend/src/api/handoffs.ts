import { api } from './client'

export type NotificationKind = 'reply' | 'new_request' | 'claimed' | 'transferred' | 'closed' | 'reopened'

export interface Notification {
  handoff_id: string
  department_id: string
  kind: NotificationKind
  activity_at: string
  unread_messages: number
  role: 'requester' | 'staff'
}

export interface HandoffMessage {
  sequence: number
  author_role: string
  mine: boolean
  author_id: string
  body: string
  created_at: string
}

export interface Handoff {
  handoff_id: string
  department_id: string
  status: 'new' | 'claimed' | 'closed'
  summary: string
  conversation_id?: string
}

export const getUnreadCount = () => api<{ unread_handoffs: number }>('/api/notifications/count')
export const getNotifications = () =>
  api<{ unread_handoffs: number; items: Notification[] }>('/api/notifications?limit=20')
export const getHandoff = (id: string) => api<Handoff>(`/api/handoffs/${id}`)
export const listHandoffMessages = (id: string) =>
  api<{ items: HandoffMessage[] }>(`/api/handoffs/${id}/messages?limit=100`)
export const postHandoffMessage = (id: string, body: string) =>
  api<unknown>(`/api/handoffs/${id}/messages`, { method: 'POST', body: JSON.stringify({ body }) })
export const markHandoffRead = (id: string, upTo: number | null) =>
  api<unknown>(`/api/handoffs/${id}/read`, { method: 'POST', body: JSON.stringify({ up_to_sequence: upTo }) })

export const createHandoff = (p: {
  requestKey: string; conversationId: string; turnSequence: number; departmentId: string; summary: string
}) =>
  api<Handoff>('/api/handoffs', {
    method: 'POST',
    body: JSON.stringify({
      request_key: p.requestKey,
      conversation_id: p.conversationId,
      turn_sequence: p.turnSequence,
      department_id: p.departmentId,
      summary: p.summary,
    }),
  })
