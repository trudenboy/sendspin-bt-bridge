import { apiGet, apiPost } from './client'
import { useIngress } from '@/composables/useIngress'
import type { DiagnosticsData, RecoveryData, OperatorGuidance } from './types'

export function getDiagnostics() {
  return apiGet<DiagnosticsData>('/api/diagnostics')
}

export function getRecoveryAssistant() {
  return apiGet<RecoveryData>('/api/recovery/assistant')
}

export function getRecoveryTimeline() {
  return apiGet<Record<string, unknown>[]>('/api/recovery/timeline')
}

export function getOperatorGuidance() {
  return apiGet<OperatorGuidance>('/api/operator/guidance')
}

export function getOnboardingAssistant() {
  return apiGet<Record<string, unknown>>('/api/onboarding/assistant')
}

export function getPreflight() {
  return apiGet<Record<string, unknown>>('/api/preflight')
}

export function getBugreport() {
  return apiGet<Record<string, unknown>>('/api/bugreport')
}

export function submitBugreport(data: {
  title: string
  description: string
  include_diagnostics: boolean
}) {
  return apiPost<{ success: boolean; issue_url?: string }>(
    '/api/bugreport/submit',
    data,
  )
}

export function rerunChecks(checkName: string) {
  return apiPost<Record<string, unknown>>('/api/checks/rerun', {
    check: checkName,
  })
}

export function downloadBugreport() {
  const { apiBase } = useIngress()
  window.location.href = `${apiBase}/api/bugreport/download`
}
