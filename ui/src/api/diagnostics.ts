import { api, apiUrl, unwrap } from './client'

export function getDiagnostics() {
  return unwrap(api().GET('/api/v1/diagnostics'))
}

export function getRecoveryAssistant() {
  return unwrap(api().GET('/api/v1/diagnostics/recovery'))
}

export function getRecoveryTimeline() {
  return unwrap(api().GET('/api/v1/diagnostics/timeline'))
}

export function getOperatorGuidance() {
  return unwrap(api().GET('/api/v1/diagnostics/guidance'))
}

export function getOnboardingAssistant() {
  return unwrap(api().GET('/api/v1/diagnostics/onboarding'))
}

export function getPreflight() {
  return unwrap(api().GET('/api/v1/bridge/preflight'))
}

export interface BugreportData {
  markdown_short: string
  text_full: string
  suggested_description: string
  report: Record<string, unknown>
}

export async function getBugreport() {
  return (await unwrap(api().GET('/api/v1/diagnostics/bug-report'))) as unknown as BugreportData
}

export function checkProxyAvailable() {
  return unwrap(api().GET('/api/v1/diagnostics/bug-report/proxy'))
}

export function submitBugreport(data: { title: string; description: string; email: string; diagnostics_text?: string }) {
  return unwrap(api().POST('/api/v1/diagnostics/bug-report', { body: data }))
}

export function rerunCheck(checkKey: string, deviceNames?: string[]) {
  return unwrap(
    api().POST('/api/v1/diagnostics/checks/{check_key}/run', {
      params: { path: { check_key: checkKey } },
      body: { device_names: deviceNames ?? null },
    }),
  )
}

export function downloadBugreport() {
  window.location.href = apiUrl('/api/v1/diagnostics/report')
}

export function downloadTimelineCsv() {
  window.location.href = apiUrl('/api/v1/diagnostics/timeline.csv')
}

export interface LogsResponse {
  logs: string[]
  runtime: string
  has_recent_issues: boolean
  recent_issue_count: number
  recent_issue_level: string
}

export async function getLogs(lines = 200) {
  return (await unwrap(api().GET('/api/v1/diagnostics/logs', { params: { query: { lines } } }))) as unknown as LogsResponse
}

export function downloadLogs() {
  window.location.href = apiUrl('/api/v1/diagnostics/logs.txt')
}

export { setLogLevel } from './status'
