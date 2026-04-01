import { apiGet, apiPost } from './client'
import type { AdapterInfo, AsyncJob, BtDeviceInfo, BtScanDevice, PairedDevice } from './types'

export function getAdapters() {
  return apiGet<AdapterInfo[]>('/api/bt/adapters')
}

export async function getPairedDevices(): Promise<PairedDevice[]> {
  const res = await apiGet<{ devices: PairedDevice[] }>('/api/bt/paired')
  return res.devices
}

export function getBtDeviceInfo(mac: string) {
  return apiPost<BtDeviceInfo>('/api/bt/info', { mac })
}

export function setDeviceManagement(playerName: string, managed: boolean) {
  return apiPost<{ success: boolean; message: string; enabled: boolean }>(
    '/api/bt/management',
    { player_name: playerName, enabled: managed },
  )
}

export function resetAndReconnect(mac: string, adapter?: string) {
  return apiPost<{ job_id: string }>('/api/bt/reset_reconnect', {
    mac,
    adapter,
  })
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
