import { api, unwrap } from './client'
import type { AuthMethod } from './types'

export function getSession() {
  return unwrap(api().GET('/api/v1/auth/session'))
}

export function signIn(body: { method?: AuthMethod; username?: string; password?: string; flow_id?: string; code?: string }) {
  return unwrap(api().POST('/api/v1/auth/session', { body: { method: 'password', ...body } }))
}

export function signOut() {
  return unwrap(api().DELETE('/api/v1/auth/session'))
}

export function setPassword(password: string, currentPassword = '') {
  return unwrap(api().PUT('/api/v1/auth/password', { body: { password, current_password: currentPassword } }))
}

export function listTokens() {
  return unwrap(api().GET('/api/v1/auth/tokens'))
}

export function issueToken(label: string) {
  return unwrap(api().POST('/api/v1/auth/tokens', { body: { label } }))
}

export function revokeToken(tokenId: string) {
  return unwrap(api().DELETE('/api/v1/auth/tokens/{token_id}', { params: { path: { token_id: tokenId } } }))
}
