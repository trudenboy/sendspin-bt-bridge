import type { Device } from '@/api/types'

type BackendAware = Pick<Device, 'backend' | 'audio' | 'bluetooth'>

/** A player that plays to a local audio output rather than a Bluetooth speaker. */
export function isPulseSink(device: Pick<Device, 'backend'>): boolean {
  return device.backend?.type === 'pulse_sink'
}

/** The output of a local audio output player is gone (unplugged or renamed away). */
export function isOutputMissing(device: BackendAware): boolean {
  return isPulseSink(device) && device.audio.sink_missing === true
}

/**
 * Whether the player can play right now: a Bluetooth speaker is connected; a
 * local audio output has its sink present.
 */
export function isDeviceConnected(device: BackendAware): boolean {
  if (isPulseSink(device)) return device.audio.has_sink === true && !device.audio.sink_missing
  return device.bluetooth?.connected === true
}
