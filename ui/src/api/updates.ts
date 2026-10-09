import { api, unwrap } from './client'

export interface UpdateInfo {
  update_available: boolean
  runtime: 'systemd' | 'ha_addon' | 'docker' | 'unknown' | string
  auto_update: boolean
  channel: string
  channel_warning?: string | null
  version?: string
  tag?: string
  url?: string
  body?: string
  published_at?: string
  current_version?: string
  prerelease?: boolean
  update_method: 'one_click' | 'ha_store' | 'manual'
  instructions?: string
  command?: string
  delivery_channel?: string | null
  delivery_slug?: string | null
  delivery_name?: string | null
  channel_switch_required?: boolean
}

export interface UpdateApplyResult {
  success?: boolean
  message?: string
  started?: boolean
  already_running?: boolean
  unit?: string
}

export type UpdateChannel = 'stable' | 'rc' | 'beta'

export async function getUpdateInfo() {
  return (await unwrap(api().GET("/api/v1/updates"))) as unknown as UpdateInfo
}

/** Starts a check; the job's result has the same shape as ``UpdateInfo``'s availability fields. */
export function startUpdateCheck(channel?: UpdateChannel) {
  return unwrap(api().POST('/api/v1/updates/check', { body: { channel: channel ?? null } }))
}

export async function applyUpdate(ref?: string, channel?: UpdateChannel) {
  return (await unwrap(
    api().POST('/api/v1/updates/apply', { body: { ref: ref ?? null, channel: channel ?? null } }),
  )) as UpdateApplyResult
}
