import type { Device } from '@/api/types'
import { isDeviceConnected } from './deviceBackend'

export interface FleetSummary {
  total: number
  connected: number
  playing: number
}

/** How many of the bridge's speakers are up and how many play (header row and Home summary). */
export function fleetSummary(devices: Device[]): FleetSummary {
  return {
    total: devices.length,
    connected: devices.filter(isDeviceConnected).length,
    playing: devices.filter((d) => d.playback.playing && d.audio.streaming).length,
  }
}
