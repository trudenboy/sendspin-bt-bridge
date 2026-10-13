import type { Device } from '@/api/types'

/** Whether the player's backend can do ``action`` (``Device.supported_actions``). */
export function canDo(device: Pick<Device, 'supported_actions'>, action: string): boolean {
  return (device.supported_actions ?? []).includes(action)
}
