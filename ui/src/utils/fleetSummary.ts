import type { Device } from '@/api/types'
import { isDeviceConnected } from './deviceBackend'

export interface FleetSummary {
  /** Speakers the bridge is meant to run: enabled and not handed over by the user. */
  total: number
  connected: number
  /** Connected or parked on standby on purpose: what the header pill's colour counts. */
  ok: number
  playing: number
}

/**
 * Disabled speakers and those the user handed over are left out of the count,
 * as stable 2.76 does; one released automatically still counts, since the
 * bridge wants it back.
 */
function isActive(d: Device): boolean {
  if (!d.enabled) return false
  return d.bluetooth?.management_enabled !== false || d.bluetooth?.released_by === 'auto'
}

/** How many of the bridge's speakers are up and how many play (header row and Home summary). */
export function fleetSummary(devices: Device[]): FleetSummary {
  const active = devices.filter(isActive)
  return {
    total: active.length,
    connected: active.filter(isDeviceConnected).length,
    ok: active.filter((d) => isDeviceConnected(d) || d.bluetooth?.standby === true).length,
    playing: devices.filter((d) => d.playback.playing && d.audio.streaming).length,
  }
}
