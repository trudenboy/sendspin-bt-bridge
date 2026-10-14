import type { Device } from '@/api/types'
import { isPulseSink } from './deviceBackend'
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
  return canDo(d, 'power_save') && d.bluetooth?.power_save !== true && d.bluetooth?.standby !== true
}
