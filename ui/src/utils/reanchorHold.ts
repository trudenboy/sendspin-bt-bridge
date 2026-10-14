import type { Device } from '@/api/types'

/**
 * Stable 2.76 keeps "Re-anchoring" on screen for a moment after the daemon
 * finishes one (5 s, or the static delay when longer), so a short correction
 * is not a flicker nobody sees. That needs memory across status updates; it
 * is kept per device id here, shared by every chip showing that device.
 */
interface Memory {
  count: number
  at: number | null
  reanchoring: boolean
  until: number | null
}

const memory = new Map<string, Memory>()

const MIN_HOLD_MS = 5000

/** For tests: forget every device. */
export function resetReanchorMemory() {
  memory.clear()
}

/**
 * Records this status update and returns until when (epoch ms) the badge
 * should still say "Re-anchoring" although the daemon no longer does, or
 * null. While the daemon re-anchors it returns null: the live flag shows it.
 */
export function observeReanchor(d: Device, now = Date.now()): number | null {
  const count = d.timing.reanchor_count ?? 0
  const at = d.timing.last_reanchor_at ?? null
  const previous = memory.get(d.id)
  const playing = d.playback.playing && d.audio.streaming
  const reanchoring = playing && d.timing.reanchoring === true
  let until = previous?.until ?? null

  if (!playing || reanchoring) {
    until = null
  } else if (previous) {
    const ended = previous.reanchoring
    const missed = count > previous.count || (at != null && at !== previous.at)
    if (ended || missed) until = now + Math.max(Math.abs(d.audio.static_delay_ms ?? 0), MIN_HOLD_MS)
  }
  if (until != null && now >= until) until = null
  memory.set(d.id, { count, at, reanchoring, until })
  return until
}
