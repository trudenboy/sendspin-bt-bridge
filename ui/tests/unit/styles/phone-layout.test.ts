import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// Regression guard (UX stage 8): nothing sideways-scrolls at 375 px.
//
// Choice: a source lint instead of a browser test. Playwright and a browser
// are not dev dependencies of the SPA (the vitest environment is happy-dom,
// which has no layout, so scrollWidth is always 0). The usual cause of a
// sideways scroll is a fixed width or min-width wider than the phone, so the
// lint forbids those without a breakpoint prefix. A phone content width is
// 375 px minus two 16 px gutters = 343 px.

const LIMIT_PX = 343
const NAMED_WIDTH_PX: Record<string, number> = { xs: 320, sm: 384, md: 448, lg: 512, xl: 576, '2xl': 672, '3xl': 768, '4xl': 896, '5xl': 1024, '6xl': 1152, '7xl': 1280 }

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) return files(p)
    return /\.(vue|ts)$/.test(f) ? [p] : []
  })
}

/** Pixels demanded by a width utility class, or 0 when it is not a fixed width. */
export function fixedWidthPx(token: string): number {
  const m = /^!?(?:min-w|w)-(?:\[(\d+(?:\.\d+)?)(px|rem|em)\]|(\d+(?:\.\d+)?)|(xs|sm|md|lg|xl|[2-7]xl))$/.exec(token)
  if (!m) return 0
  if (m[1]) return Number(m[1]) * (m[2] === 'px' ? 1 : 16)
  if (m[3]) return Number(m[3]) * 4
  return NAMED_WIDTH_PX[m[4]!] ?? 0
}

describe('phone layout lint', () => {
  it('knows a fixed width when it sees one', () => {
    expect(fixedWidthPx('min-w-[400px]')).toBe(400)
    expect(fixedWidthPx('w-96')).toBe(384)
    expect(fixedWidthPx('min-w-md')).toBe(448)
    expect(fixedWidthPx('w-[30rem]')).toBe(480)
    expect(fixedWidthPx('min-w-[200px]')).toBe(200)
    expect(fixedWidthPx('w-full')).toBe(0)
    expect(fixedWidthPx('sm:min-w-[400px]')).toBe(0)
  })

  it('has no fixed width wider than a phone without a breakpoint prefix', () => {
    const hits: string[] = []
    for (const file of files(join(__dirname, '../../../src'))) {
      const text = readFileSync(file, 'utf8')
      for (const m of text.matchAll(/[^\s"'`<>=]+/g)) {
        const px = fixedWidthPx(m[0])
        if (px > LIMIT_PX) hits.push(`${file.split('/src/')[1]}: ${m[0]} (${px}px)`)
      }
      for (const m of text.matchAll(/(?<![(\w-])(?:min-)?width:\s*(\d+)px/g))
        if (Number(m[1]) > LIMIT_PX) hits.push(`${file.split('/src/')[1]}: ${m[0]}`)
    }
    expect(hits).toEqual([])
  })
})
