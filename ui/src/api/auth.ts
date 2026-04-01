import { apiGet, apiPost } from './client'
import type { AuthCheckResponse } from './types'

export function checkAuth() {
  return apiGet<AuthCheckResponse>('/api/auth/check')
}

export function login(password: string) {
  return apiPost<{ success: boolean; error?: string }>('/api/auth/login', {
    password,
  })
}

export function logout() {
  return apiPost<{ success: boolean }>('/api/auth/logout')
}
