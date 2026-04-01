import { apiGet, apiPost } from './client'
import type { AdapterInfo, AsyncJob, BtScanDevice } from './types'

export function getAdapters() {
  return apiGet<AdapterInfo[]>('/api/bt/adapters')
}

export function getPairedDevices() {
  return apiGet<
    { mac: string; name: string; trusted: boolean; class: string }[]
  >('/api/bt/paired')
}

export function startScan(adapter?: string) {
  return apiPost<AsyncJob>('/api/bt/scan', { adapter })
}

export function getScanResult(jobId: string) {
  return apiGet<AsyncJob<BtScanDevice[]>>(`/api/bt/scan/result/${jobId}`)
}

export function pairDevice(mac: string, adapter?: string) {
  return apiPost<{ success: boolean; error?: string }>('/api/bt/pair', {
    mac,
    adapter,
  })
}

export function reconnectDevice(mac: string) {
  return apiPost<{ success: boolean; error?: string }>('/api/bt/reconnect', {
    mac,
  })
}

export function disconnectDevice(mac: string) {
  return apiPost<{ success: boolean; error?: string }>('/api/bt/disconnect', {
    mac,
  })
}

export function removeDevice(mac: string) {
  return apiPost<{ success: boolean; error?: string }>('/api/bt/remove', {
    mac,
  })
}

export function setDeviceEnabled(playerName: string, enabled: boolean) {
  return apiPost<{ success: boolean }>('/api/device/enabled', {
    player_name: playerName,
    enabled,
  })
}

export function wakeDevice(mac: string) {
  return apiPost<{ success: boolean }>('/api/bt/wake', { mac })
}

export function standbyDevice(mac: string) {
  return apiPost<{ success: boolean }>('/api/bt/standby', { mac })
}

export function toggleAdapterPower(adapter: string) {
  return apiPost<{ success: boolean; powered: boolean }>(
    '/api/bt/adapter/power',
    { adapter },
  )
}

export function rebootAdapter(adapter: string) {
  return apiPost<{ success: boolean }>('/api/bt/adapter/reboot', { adapter })
}
