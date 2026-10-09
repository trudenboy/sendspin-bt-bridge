/* Volume, mute, pause/play and native transport. */
import { api, unwrap } from './client'
import type { TransportCommand } from './types'

const path = (deviceId: string) => ({ params: { path: { device_id: deviceId } } })

export function setVolume(deviceId: string, level: number) {
  return unwrap(api().PUT('/api/v1/devices/{device_id}/volume', { ...path(deviceId), body: { level } }))
}

/** ``muted`` omitted toggles. */
export function setMute(deviceId: string, muted?: boolean) {
  return unwrap(api().PUT('/api/v1/devices/{device_id}/mute', { ...path(deviceId), body: { muted: muted ?? null } }))
}

export function setDevicePlayback(deviceId: string, action: 'pause' | 'play') {
  return unwrap(api().POST('/api/v1/devices/{device_id}/playback', { ...path(deviceId), body: { action } }))
}

export function transport(deviceId: string, command: TransportCommand, value?: unknown) {
  return unwrap(api().POST('/api/v1/devices/{device_id}/transport', { ...path(deviceId), body: { command, value } }))
}

export function setGroupVolume(groupId: string, level: number) {
  return unwrap(
    api().PUT('/api/v1/groups/{group_id}/volume', { params: { path: { group_id: groupId } }, body: { level } }),
  )
}

export function setGroupPlayback(groupId: string, action: 'pause' | 'play') {
  return unwrap(
    api().POST('/api/v1/groups/{group_id}/playback', { params: { path: { group_id: groupId } }, body: { action } }),
  )
}

export function setAllPlayback(action: 'pause' | 'play') {
  return unwrap(api().POST('/api/v1/playback', { body: { action } }))
}
