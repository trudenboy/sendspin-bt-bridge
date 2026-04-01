import { apiGet, apiPost } from './client'
import type { AsyncJob, NowPlaying, SyncGroup } from './types'

export function discoverMA() {
  return apiPost<AsyncJob>('/api/ma/discover')
}

export function getDiscoverResult(jobId: string) {
  return apiGet<AsyncJob<{ servers: { url: string; version: string }[] }>>(
    `/api/ma/discover/result/${jobId}`,
  )
}

export function getGroups() {
  return apiGet<SyncGroup[]>('/api/ma/groups')
}

export function getNowPlaying() {
  return apiGet<Record<string, NowPlaying>>('/api/ma/nowplaying')
}

export function queueCmd(
  action: string,
  groupId?: string,
  params?: Record<string, unknown>,
) {
  return apiPost<AsyncJob>('/api/ma/queue/cmd', {
    action,
    group_id: groupId,
    ...params,
  })
}

export function maLogin(haToken: string) {
  return apiPost<{ success: boolean; url?: string }>('/api/ma/ha-login', {
    ha_token: haToken,
  })
}

export function maReload() {
  return apiPost<{ success: boolean }>('/api/ma/reload')
}

export function silentAuth(haToken: string, maUrl: string) {
  return apiPost<{ success: boolean; url?: string; username?: string; message?: string }>(
    '/api/ma/ha-silent-auth',
    { ha_token: haToken, ma_url: maUrl },
  )
}
