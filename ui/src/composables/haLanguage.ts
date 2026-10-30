/**
 * The Home Assistant user's language, for the first visit inside the add-on panel.
 *
 * Ingress carries no language information (Supervisor forwards only the user's
 * id and name), so the page asks the browser. Sources, most reliable first:
 *   1. ``<html lang>`` of the Home Assistant page around us (same origin under
 *      ingress): the language HA itself renders with, profile choice included.
 *   2. HA's per-browser ``selectedLanguage`` entry in the same origin's storage.
 *   3. The browser's own languages, when we are in a frame but cannot read the
 *      parent (another origin).
 * Outside a frame there is no Home Assistant around us: null, English stays the default.
 */
interface Env {
  parent: Window
  self: Window
  navigatorLanguages?: readonly string[]
}

function fromHost(parent: Window): string | null {
  try {
    const lang = parent.document?.documentElement?.lang?.trim()
    if (lang) return lang
  } catch {
    return null
  }
  try {
    const raw = parent.localStorage?.getItem('selectedLanguage')
    if (raw) {
      const value = JSON.parse(raw)
      if (typeof value === 'string' && value) return value
    }
  } catch {
    /* unreadable or malformed: ignore */
  }
  return null
}

export function haLanguage(env?: Env): string | null {
  const parent = env ? env.parent : window.parent
  const self = env ? env.self : window
  if (parent === self) return null
  const host = fromHost(parent)
  if (host) return host
  const langs = env?.navigatorLanguages ?? navigator.languages
  return langs?.[0] ?? null
}
