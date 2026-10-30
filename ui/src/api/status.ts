import { api, unwrap } from './client'

export function getStatus() {
  return unwrap(api().GET('/api/v1/status'))
}

export function getHealth() {
  return unwrap(api().GET('/api/v1/health'))
}

export function getStartupProgress() {
  return unwrap(api().GET('/api/v1/bridge/startup'))
}

export function getRuntimeInfo() {
  return unwrap(api().GET('/api/v1/bridge/runtime'))
}

export function restartBridge() {
  return unwrap(api().POST('/api/v1/bridge/restart'))
}

export function setSourceEnabled(enabled: boolean) {
  return unwrap(api().POST('/api/v1/bridge/source', { body: { enabled } }))
}

export function setSourcePlayer(deviceId: string, enabled: boolean) {
  return unwrap(api().POST('/api/v1/bridge/source/players', { body: { device_id: deviceId, enabled } }))
}

export function openSourcePairing(seconds = 120) {
  return unwrap(api().POST('/api/v1/bridge/source/pairing', { body: { seconds } }))
}

export function closeSourcePairing() {
  return unwrap(api().DELETE('/api/v1/bridge/source/pairing'))
}

export function setLogLevel(level: 'INFO' | 'DEBUG') {
  return unwrap(api().PUT('/api/v1/bridge/log-level', { body: { level } }))
}
