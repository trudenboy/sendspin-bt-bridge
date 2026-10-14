import type { Device } from '@/api/types'
import { isDeviceConnected, isPulseSink } from './deviceBackend'
import { canDo } from './supportedActions'

/** Power actions the selection bar applies to many speakers at once. */
export type BulkAction = 'power_save' | 'standby' | 'release'

/**
 * Whether ``action`` applies to this device, mirroring the per-device controls
 * (stable 2.76's bulk gate): it is enabled and still managed by the bridge,
 * its backend supports the action, and it is not already in that state.
 */
export function bulkEligible(d: Device, action: BulkAction): boolean {
  if (!d.enabled || d.bluetooth?.management_enabled === false) return false
  if (action === 'release') return !isPulseSink(d) && d.bluetooth != null
  if (action === 'standby') return canDo(d, 'standby') && d.bluetooth?.standby !== true
  // A local output reports no power-save state (``bluetooth`` is null), so it
  // always counts as eligible; the bridge treats a repeat as a no-op.
  return canDo(d, 'power_save') && d.bluetooth?.power_save !== true && d.bluetooth?.standby !== true
}

/** Whether Mute all can reach this speaker: enabled, connected, with a sink to mute. */
export function canMute(d: Device): boolean {
  return d.enabled && isDeviceConnected(d) && d.audio.has_sink
}

/** Whether Pause all can reach this speaker: enabled, managed, connected and not on standby. */
export function canPause(d: Device): boolean {
  return (
    d.enabled &&
    d.bluetooth?.management_enabled !== false &&
    d.bluetooth?.standby !== true &&
    isDeviceConnected(d)
  )
}
