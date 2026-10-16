/** A spread or delay in ms for display: at most one decimal, no trailing zero ("12", "2.5"). */
export function formatMs(ms: number): string {
  return String(Math.round(ms * 10) / 10)
}

/** Seconds as m:ss ("2:41"). */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** A signed change in ms: "+12", "−20" (a real minus sign), "0". */
export function formatDelta(ms: number): string {
  if (ms > 0) return `+${ms}`
  if (ms < 0) return `−${Math.abs(ms)}`
  return '0'
}
