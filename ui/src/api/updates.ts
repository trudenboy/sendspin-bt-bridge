import { apiGet, apiPost } from './client'
import type { AsyncJob } from './types'

/* ── Response types ─────────────────────────────────────────────── */

export interface UpdateInfo {
  update_available: boolean
  runtime: 'systemd' | 'ha_addon' | 'docker' | 'unknown'
  auto_update: boolean
  channel: string
  channel_warning?: string | null
  /** Present when update_available is true */
  version?: string
  tag?: string
  url?: string
  body?: string
  published_at?: string
  current_version?: string
  prerelease?: boolean
  /** Platform-specific update guidance */
  update_method: 'one_click' | 'ha_store' | 'manual'
  instructions?: string
  command?: string
  /** HA addon delivery info */
  delivery_channel?: string | null
  delivery_slug?: string | null
  delivery_name?: string | null
  channel_switch_required?: boolean
}

export interface UpdateCheckJob extends AsyncJob<UpdateInfo> {
  channel?: string
}

export interface UpdateApplyResult {
  success: boolean
  message?: string
  error?: string
  started?: boolean
  already_running?: boolean
  unit?: string
}

/* ── API functions ──────────────────────────────────────────────── */

/** Get cached update availability + runtime instructions. */
export function getUpdateInfo() {
  return apiGet<UpdateInfo>('/api/update/info')
}

/** Start async update check (returns job_id for polling). */
export function startUpdateCheck(channel?: string) {
  return apiPost<UpdateCheckJob>('/api/update/check', channel ? { channel } : null)
}

/** Poll for update check result. */
export function getUpdateCheckResult(jobId: string) {
  return apiGet<UpdateCheckJob>(`/api/update/check/result/${jobId}`)
}

/** Apply update (systemd/LXC only). */
export function applyUpdate(tag?: string, channel?: string) {
  const body: Record<string, string> = {}
  if (tag) body.tag = tag
  if (channel) body.channel = channel
  return apiPost<UpdateApplyResult>('/api/update/apply', body)
}
