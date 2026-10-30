import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import router from '@/router'

// Companion of the named-navigation check in router-guard.test.ts: path
// targets ('/diagnostics', to="/groups", nav links) must land on a real page
// too, and every page in the navigation must be a real route.

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) return sources(p)
    return /\.(ts|vue)$/.test(f) ? [p] : []
  })
}

describe('path navigation', () => {
  it('only targets paths that match a route', () => {
    const paths = new Set<string>()
    for (const file of sources(join(__dirname, '../../src')))
      for (const m of readFileSync(file, 'utf8').matchAll(/(?:push|replace)\(\s*'(\/[^'?#]*)'|\bto="(\/[^"?#]*)"|\bto:\s*'(\/[^'?#]*)'/g))
        paths.add((m[1] ?? m[2] ?? m[3])!)
    expect(paths.size).toBeGreaterThan(3)
    const unmatched = [...paths].filter((p) => router.resolve(p).matched.length === 0)
    expect(unmatched).toEqual([])
  })

  it('keeps the earlier addresses working', () => {
    for (const old of ['/devices', '/ma', '/settings']) expect(router.resolve(old).matched.length).toBeGreaterThan(0)
  })
})
