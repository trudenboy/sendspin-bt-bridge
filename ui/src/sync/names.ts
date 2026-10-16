import { speakerName as stripBridge } from '@/utils/speakerName'

/**
 * A speaker's name as people read it in the sync screens: without the bridge suffix
 * ("ENEBY Portable @ 85b1ecde-…" → "ENEBY Portable") and without trailing technical ids such as a
 * web kiosk's "(wk_723e2…) [192.168.10.229]". The full name goes into ``title`` where it is shown.
 */
export function speakerName(name: string | null | undefined, fallback = ''): string {
  let out = stripBridge(name, fallback)
  for (;;) {
    const next = out.replace(/\s*\[[^\]\s]*\]$/, '').replace(/\s*\([^()\s]*[\d_.][^()\s]*\)$/, '').trim()
    if (next === out || !next) break
    out = next
  }
  return out || fallback
}
