/**
 * Players are registered as "Speaker @ bridge" so Music Assistant can tell
 * bridges apart; inside one bridge's own UI the suffix is noise.
 */
export function speakerName(name: string | null | undefined, fallback = ''): string {
  const full = (name ?? '').trim()
  if (!full) return fallback
  const at = full.lastIndexOf(' @ ')
  return at > 0 ? full.slice(0, at) : full
}
