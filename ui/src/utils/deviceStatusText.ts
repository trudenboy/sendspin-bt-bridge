import type { Device } from '@/api/types'

/** Whole minutes the speaker has been on standby, or null when unknown. */
export function standbyElapsedMinutes(d: Device, now: number): number | null {
  const bt = d.bluetooth
  if (!bt?.standby || !bt.standby_since) return null
  const since = Date.parse(bt.standby_since)
  if (Number.isNaN(since) || since > now) return null
  return Math.floor((now - since) / 60_000)
}

/** Who let the speaker go: the bridge after connection trouble ('auto') or the user; null while managed. */
export function releaseKind(d: Device): 'auto' | 'user' | null {
  const bt = d.bluetooth
  if (!bt || bt.management_enabled !== false) return null
  return bt.released_by === 'auto' ? 'auto' : 'user'
}
