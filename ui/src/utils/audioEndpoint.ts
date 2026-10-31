/* Small pure helpers for the audio half of the Hardware page. */

/** Three answers a port's availability can give; unknown is not unavailable. */
export type PortAvailability = 'available' | 'unavailable' | 'unknown'

/** A card port's tri-state ``available`` flag. */
export function portAvailability(available: boolean | null | undefined): PortAvailability {
  if (available === true) return 'available'
  if (available === false) return 'unavailable'
  return 'unknown'
}

/** A sink/source ``active_port_available`` string, or null when it has no active port. */
export function activePortAvailability(value: string | null | undefined): PortAvailability | null {
  if (!value) return null
  if (value === 'available') return 'available'
  if (value === 'not available') return 'unavailable'
  return 'unknown'
}

/** Badge tone for an availability answer: success, error and neutral must look different. */
export function availabilityTone(availability: PortAvailability): 'success' | 'error' | 'neutral' {
  if (availability === 'available') return 'success'
  if (availability === 'unavailable') return 'error'
  return 'neutral'
}

/** Badge tone for an endpoint's state. */
export function endpointStateTone(state: string): 'success' | 'neutral' | 'warning' {
  if (state === 'running') return 'success'
  if (state === 'suspended') return 'warning'
  return 'neutral'
}

/** What an input is: the phone over Bluetooth, a monitor, a line-in, or a microphone. */
export type InputKind = 'phone' | 'monitor' | 'lineIn' | 'microphone'

/**
 * Classify a source from the facts the read model carries. The audio server
 * names a Bluetooth capture ``bluez_source.<MAC>``; a monitor ends in
 * ``.monitor``; a line-in says so in its name or description. Everything else
 * with a capture direction is a microphone — the safe default, since the
 * alternative is an empty row.
 */
export function inputKind(source: {
  name: string
  description?: string
  owner?: { bluetooth?: string | null; card?: string | null } | null
}): InputKind {
  const text = `${source.name} ${source.description ?? ''}`.toLowerCase()
  if (source.owner?.bluetooth || source.name.startsWith('bluez_source.')) return 'phone'
  if (text.includes('monitor')) return 'monitor'
  if (/line[\s_-]?in|line[\s_-]?level|aux(?:iliary)?/.test(text)) return 'lineIn'
  return 'microphone'
}
