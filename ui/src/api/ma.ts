/* Music Assistant: connection, sign-in, discovery, groups, queue. */
import { api, unwrap } from './client'
import type { QueueAction } from './types'

export function getConnection() {
  return unwrap(api().GET('/api/v1/music-assistant'))
}

export function signInWithPassword(url: string, username: string, password: string) {
  return unwrap(api().POST('/api/v1/music-assistant/session', { body: { url, username, password } }))
}

export function signInHa(body: { ma_url: string; step?: 'init' | 'mfa'; username?: string; password?: string; code?: string }) {
  return unwrap(api().POST('/api/v1/music-assistant/session/ha', { body }))
}

export function silentAuth(haToken: string, maUrl: string) {
  return unwrap(api().POST('/api/v1/music-assistant/session/ha-silent', { body: { ha_token: haToken, ma_url: maUrl } }))
}

export function discoverMA() {
  return unwrap(api().POST('/api/v1/music-assistant/discovery'))
}

export function refreshGroups() {
  return unwrap(api().POST('/api/v1/music-assistant/groups/refresh'))
}

export function maReload() {
  return unwrap(api().POST('/api/v1/music-assistant/reload'))
}

export function getGroups() {
  return unwrap(api().GET('/api/v1/music-assistant/groups'))
}

export function getNowPlaying() {
  return unwrap(api().GET('/api/v1/music-assistant/now-playing'))
}

export function queueCommand(
  action: QueueAction,
  target: { device_id?: string; syncgroup_id?: string; group_id?: string },
  value?: unknown,
) {
  return unwrap(api().POST('/api/v1/music-assistant/queue-commands', { body: { action, value, ...target } }))
}
