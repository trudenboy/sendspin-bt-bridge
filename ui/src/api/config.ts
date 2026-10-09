import { api, apiUrl, unwrap } from './client'
import type { BridgeConfig } from './types'

export function getConfig() {
  return unwrap(api().GET('/api/v1/config'))
}

export function saveConfig(config: Partial<BridgeConfig>) {
  return unwrap(api().PUT('/api/v1/config', { body: config as never }))
}

export function validateConfig(config: Partial<BridgeConfig>) {
  return unwrap(api().POST('/api/v1/config/validate', { body: config as never }))
}

export function downloadConfig() {
  window.location.href = apiUrl('/api/v1/config/export')
}

export function uploadConfig(file: File) {
  const form = new FormData()
  form.append('file', file)
  return unwrap(
    api().POST('/api/v1/config/import', {
      body: form as never,
      bodySerializer: (body: unknown) => body as FormData,
    }),
  )
}

export function testSendspin(server?: string, port?: number | string) {
  return unwrap(
    api().POST('/api/v1/config/sendspin-test', { body: { SENDSPIN_SERVER: server ?? null, SENDSPIN_PORT: port ?? null } }),
  )
}
