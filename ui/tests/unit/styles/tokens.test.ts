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
