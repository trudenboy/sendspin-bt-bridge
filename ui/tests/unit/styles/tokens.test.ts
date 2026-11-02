import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(__dirname, '../../../src/app.css'), 'utf8')

function block(selector: RegExp): string {
  const m = css.match(selector)
  if (!m) throw new Error(`block not found: ${selector}`)
  return m[1]
}
const light = block(/\n:root \{([\s\S]*?)\n\}/)
const dark = block(/\n\.dark \{([\s\S]*?)\n\}/)

function token(scope: string, name: string, fallbackScope?: string): string {
  const m = scope.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (m) return m[1]
  if (fallbackScope !== undefined) return token(fallbackScope, name)
  throw new Error(`token not found: ${name}`)
}

function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
  const [r, g, b] = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

describe('focus ring', () => {
  it('defines a global :focus-visible outline using a token', () => {
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--sb-focus-ring\)/)
  })
  it.each([
    ['light', light, '#ffffff', undefined],
    ['light on fill', light, '#f0f0f0', undefined],
    ['dark', dark, '#1c1c1c', light],
    ['dark on background', dark, '#111111', light],
  ])('focus ring has >=3:1 contrast (%s)', (_n, scope, bg, fb) => {
    expect(contrast(token(scope, 'sb-focus-ring', fb), bg)).toBeGreaterThanOrEqual(3)
  })
})

describe('warning text token', () => {
  it.each([
    ['light', light, ['#ffffff', '#fafafa', '#f0f0f0'], undefined],
    ['dark', dark, ['#1c1c1c', '#111111', '#282828'], light],
  ] as const)('--sb-warning-text has AA contrast (%s)', (_n, scope, bgs, fb) => {
    for (const bg of bgs) expect(contrast(token(scope, 'sb-warning-text', fb), bg)).toBeGreaterThanOrEqual(4.5)
  })
  it('exposes the token as a text-warning-text utility and uses it in tone-warning', () => {
    expect(css).toMatch(/--color-warning-text:\s*var\(--sb-warning-text\)/)
    expect(css).toMatch(/@utility tone-warning \{[^}]*color:\s*var\(--sb-warning-text\)/)
  })
})

describe('secondary and tertiary text', () => {
  it.each([
    ['light', light, ['#ffffff', '#fafafa', '#f0f0f0'], undefined],
    ['dark', dark, ['#1c1c1c', '#111111', '#282828'], light],
  ] as const)('meet 4.5:1 on surfaces (%s)', (_n, scope, bgs, fb) => {
    for (const name of ['sb-text-secondary', 'sb-text-tertiary'])
      for (const bg of bgs)
        expect(contrast(token(scope, name, fb), bg), `${name} on ${bg}`).toBeGreaterThanOrEqual(4.5)
  })
})

describe('status tones on their tinted fills', () => {
  /** Chrome's color-mix(in srgb, fg p%, bg) in 8-bit channels. */
  function mix(fg: string, bg: string, p: number): string {
    const f = fg.slice(1)
    const b = bg.slice(1)
    let out = '#'
    for (let i = 0; i < 6; i += 2) {
      const fv = parseInt(f.slice(i, i + 2), 16)
      const bv = parseInt(b.slice(i, i + 2), 16)
      out += Math.round(fv * p + bv * (1 - p))
        .toString(16)
        .padStart(2, '0')
    }
    return out
  }

  const surfaces = [
    ['card', '#ffffff'],
    ['page', '#fafafa'],
    ['fill', '#f0f0f0'],
  ] as const
  const darkSurfaces = [
    ['card', '#1c1c1c'],
    ['page', '#111111'],
    ['fill', '#282828'],
  ] as const

  it.each([
    ['success', 'sb-success', light, surfaces, undefined],
    ['info', 'sb-info', light, surfaces, undefined],
    ['error', 'sb-error', light, surfaces, undefined],
    ['primary text on info', 'sb-primary-text', light, surfaces, undefined],
  ] as const)('--%s keeps AA on a 14%% tint in light (%s)', (_label, name, scope, bgs, fb) => {
    const fg = token(scope, name, fb)
    for (const [surface, bg] of bgs) {
      expect(contrast(fg, mix(fg, bg, 0.14)), `${name} on ${surface}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it.each([
    ['success', 'sb-success', dark, darkSurfaces, light],
    ['info', 'sb-info', dark, darkSurfaces, light],
    ['error', 'sb-error', dark, darkSurfaces, light],
    ['primary text on info', 'sb-primary-text', dark, darkSurfaces, light],
  ] as const)('--%s keeps AA on a 14%% tint in dark (%s)', (_label, name, scope, bgs, fb) => {
    const fg = token(scope, name, fb)
    for (const [surface, bg] of bgs) {
      expect(contrast(fg, mix(fg, bg, 0.14)), `${name} on ${surface}`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('primary text keeps AA on plain surfaces in both themes', () => {
    for (const [scope, bgs, fb] of [
      [light, surfaces, undefined],
      [dark, darkSurfaces, light],
    ] as const) {
      const fg = token(scope, 'sb-primary-text', fb)
      for (const [surface, bg] of bgs) {
        expect(contrast(fg, bg), `primary text on ${surface}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
})
