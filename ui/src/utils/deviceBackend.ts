import type { Device } from '@/api/types'

/** A player that plays to a local audio output rather than a Bluetooth speaker. */
export function isPulseSink(device: Pick<Device, 'backend'>): boolean {
  return device.backend?.type === 'pulse_sink'
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
