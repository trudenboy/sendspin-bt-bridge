import { api, unwrap } from './client'

export interface Telemetry {
  bridge: {
    uptime_seconds?: number
    process_rss_mb?: number
    python?: string
    platform?: string
    arch?: string
    kernel?: string
    audio_server?: string
    bluez?: string
  }
  subprocesses: {
    name: string
    pid?: number | null
    alive?: boolean
    running?: boolean
    zombie_restarts?: number
    reconnecting?: boolean
    last_error?: string | null
    process_rss_mb?: number | null
  }[]
}

export interface Hook {
  id: string
  url: string
  categories: string[]
  event_types: string[]
  timeout_sec: number
  created_at: string
  last_http_status: number | null
  last_error: string | null
  success_count: number
  failure_count: number
}

export async function getTelemetry() {
  return (await unwrap(api().GET('/api/v1/bridge/telemetry'))) as unknown as Telemetry
}

export async function listHooks() {
  return ((await unwrap(api().GET('/api/v1/hooks'))) as unknown as { hooks: Hook[] }).hooks
}

export function addHook(url: string, categories: string[]) {
  return unwrap(api().POST('/api/v1/hooks', { body: { url, categories: categories.length ? categories : null } }))
}

export function removeHook(hookId: string) {
  return unwrap(api().DELETE('/api/v1/hooks/{hook_id}', { params: { path: { hook_id: hookId } } }))
}
