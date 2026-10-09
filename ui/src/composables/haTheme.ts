/**
 * Following Home Assistant's theme from inside its ingress panel.
 *
 * Ingress serves the bridge from Home Assistant's own origin, so the panel's
 * iframe can read the variables HA puts on its root element: the user's dark
 * mode and accent colour. Anywhere else (direct port, another origin) this
 * finds nothing and the page keeps its own theme.
 */

export interface HomeAssistantTheme {
  dark: boolean
  /** HA's ``--primary-color``, or null when it is not a plain colour. */
  primary: string | null
}

const COLOR = /^(#[0-9a-f]{3,8}|rgba?\([\d\s.,%/]+\)|hsla?\([\d\s.,%/deg]+\))$/i

/** The Home Assistant page around this iframe, when it shares our origin. */
export function hostDocument(): Document | null {
  try {
    if (window.parent === window) return null
    const doc = window.parent.document
    return doc?.documentElement ? doc : null
  } catch {
    // Another origin: the browser refuses access, which is the expected case.
    return null
  }
}

function cssVar(doc: Document, name: string): string {
  const root = doc.documentElement
  const inline = root.style.getPropertyValue(name).trim()
  if (inline) return inline
  const view = doc.defaultView
  return view ? view.getComputedStyle(root).getPropertyValue(name).trim() : ''
}

/** Relative luminance (0 black … 1 white) of a #hex or rgb() colour. */
function luminance(color: string): number | null {
  let rgb: number[] | null = null
  const hex = color.match(/^#([0-9a-f]{3}|[0-9a-f]{6})/i)
  if (hex) {
    const h = hex[1]!.length === 3 ? [...hex[1]!].map((c) => c + c).join('') : hex[1]!
    rgb = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16))
  } else {
    const m = color.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i)
    if (m) rgb = [m[1], m[2], m[3]].map(Number)
  }
  if (!rgb) return null
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!
}

/** HA's dark mode and accent, or null when ``doc`` is not a Home Assistant page. */
export function readHomeAssistantTheme(doc: Document): HomeAssistantTheme | null {
  const background = cssVar(doc, '--primary-background-color')
  if (!background) return null
  const lum = luminance(background)
  if (lum === null) return null
  const primary = cssVar(doc, '--primary-color')
  return { dark: lum < 0.4, primary: COLOR.test(primary) ? primary : null }
}
