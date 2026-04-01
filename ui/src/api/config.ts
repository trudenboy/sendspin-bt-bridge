import { apiGet, apiPost } from './client'
import { useIngress } from '@/composables/useIngress'
import type { BridgeConfig } from './types'

export function getConfig() {
  return apiGet<BridgeConfig>('/api/config')
}

export function saveConfig(config: Partial<BridgeConfig>) {
  return apiPost<{ success: boolean }>('/api/config', config)
}

export function validateConfig(config: Partial<BridgeConfig>) {
  return apiPost<{ valid: boolean; errors?: string[] }>(
    '/api/config/validate',
    config,
  )
}

export function downloadConfig() {
  // Use direct window.location for binary download
  const { apiBase } = useIngress()
  window.location.href = `${apiBase}/api/config/download`
}
