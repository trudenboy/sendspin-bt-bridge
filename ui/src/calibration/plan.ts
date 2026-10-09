/**
 * Turning microphone measurements into one delay correction.
 *
 * Each measurement is "target minus reference" in ms, from the same phone
 * position. The median of the good ones is the offset; if they disagree by
 * more than MAX_SPREAD_MS (median absolute deviation) the phone moved or the
 * room was noisy, and nothing is applied. The later speaker gets more
 * declared delay, so the bridge sends its audio earlier — the delay can only
 * grow, never go below zero.
 */

export const MAX_SPREAD_MS = 15
export const MAX_DELAY_MS = 5000

export interface Speaker {
  id: string
  delay: number
}

export type Plan =
  | { ok: true; deviceId: string; value: number; median: number; spread: number }
  | { ok: false; reason: 'not_detected' | 'unstable'; spread?: number }

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]!
}

export function planCorrection(estimates: number[], target: Speaker, reference: Speaker): Plan {
  const good = estimates.filter((v) => Number.isFinite(v))
  if (good.length < 2) return { ok: false, reason: 'not_detected' }
  const mid = median(good)
  const spread = median(good.map((v) => Math.abs(v - mid)))
  if (spread > MAX_SPREAD_MS) return { ok: false, reason: 'unstable', spread }
  const late = mid >= 0 ? target : reference
  const value = Math.min(MAX_DELAY_MS, Math.max(0, Math.round(late.delay + Math.abs(mid))))
  return { ok: true, deviceId: late.id, value, median: mid, spread }
}
