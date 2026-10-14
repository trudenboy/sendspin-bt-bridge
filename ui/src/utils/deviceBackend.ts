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

/**
 * An adapter as the config names it (hci name or MAC, any case), resolved
 * against the bridge's adapters; '' when it is a MAC the bridge does not know.
 */
export function resolveAdapter<A extends { id: string; mac: string }>(
  configured: string,
  adapters: A[] = [],
): { adapter: A | undefined; hci: string } {
  const wanted = configured.toUpperCase()
  const adapter = adapters.find((a) => a.id === configured || a.mac.toUpperCase() === wanted)
  return { adapter, hci: adapter?.id ?? (configured.startsWith('hci') ? configured : '') }
}
