import { apiGet } from './client'
import type { BridgeSnapshot } from './types'

export function getStatus() {
  return apiGet<BridgeSnapshot>('/api/status')
}

export function getHealth() {
  return apiGet<{ status: string }>('/api/health')
}

export function getStartupProgress() {
  return apiGet<Record<string, unknown>>('/api/startup-progress')
}

export function getRuntimeInfo() {
  return apiGet<Record<string, unknown>>('/api/runtime-info')
}
