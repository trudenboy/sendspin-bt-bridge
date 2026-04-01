import { apiGet, apiPost } from './client'

export function getDiagnostics() {
  return apiGet<Record<string, unknown>>('/api/diagnostics')
}

export function getRecoveryAssistant() {
  return apiGet<Record<string, unknown>>('/api/recovery/assistant')
}

export function getRecoveryTimeline() {
  return apiGet<Record<string, unknown>[]>('/api/recovery/timeline')
}

export function getOperatorGuidance() {
  return apiGet<Record<string, unknown>>('/api/operator/guidance')
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
