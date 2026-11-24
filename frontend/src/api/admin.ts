import { api } from './client'

export interface AdminUser { id: string; name: string; email: string; status: 'active' | 'inactive' | string; created_at: string }
export interface AdminDepartment {
  id: string
  name: string
  status: 'provisioning' | 'active' | 'failed' | 'archived' | string
  provisioning_status: string | null
  archived_at: string | null
}
export interface CompanyUsage {
  configured: boolean
  enabled: boolean
  month: string | null
  budget_micro_usd: number | null
  spent_micro_usd: number | null
  held_micro_usd: number | null
  remaining_micro_usd: number | null
  over_budget_micro_usd: number | null
  unresolved_requests: number | null
}

export const PAGE = 50

export const listUsers = (status: string, offset: number) =>
  api<AdminUser[]>(`/admin/users?limit=${PAGE}&offset=${offset}${status ? `&status=${status}` : ''}`)
export const createUser = (name: string, email: string) =>
  api<AdminUser>('/admin/users', { method: 'POST', body: JSON.stringify({ name, email }) })
export const setUserStatus = (id: string, status: 'active' | 'inactive') =>
  api<AdminUser>(`/admin/users/${id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) })

export const listDepartments = (state: string, offset: number) =>
  api<AdminDepartment[]>(`/admin/departments?limit=${PAGE}&offset=${offset}${state ? `&state=${state}` : ''}`)
export const createDepartment = (name: string) =>
  api<AdminDepartment>('/admin/departments', { method: 'POST', body: JSON.stringify({ name }) })
export const archiveDepartment = (id: string) =>
  api<AdminDepartment>(`/admin/departments/${id}/archive`, { method: 'POST' })

export const getCompanyUsage = () => api<CompanyUsage>('/admin/usage/summary')
