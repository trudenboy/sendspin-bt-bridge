/** A spread or delay in ms for display: at most one decimal, no trailing zero ("12", "2.5"). */
export function formatMs(ms: number): string {
  return String(Math.round(ms * 10) / 10)
}

/** A signed change in ms: "+12", "−20" (a real minus sign), "0". */
export function formatDelta(ms: number): string {
  if (ms > 0) return `+${ms}`
  if (ms < 0) return `−${Math.abs(ms)}`
  return '0'
}
