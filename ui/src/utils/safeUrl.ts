/**
 * Addresses that came from the server or the config are opened only when
 * they are plain http(s) — never javascript:, data: or anything malformed
 * (Music Assistant's frontend rule for user-supplied URLs).
 */
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null
  try {
    const url = new URL(raw)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

export function openExternal(raw: string | null | undefined) {
  const url = safeHttpUrl(raw)
  if (url) window.open(url, '_blank', 'noopener,noreferrer')
}
