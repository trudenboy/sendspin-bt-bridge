import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '@/i18n/en.json'

const SRC = resolve(__dirname, '../../../src')

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) return sources(p)
    return /\.(vue|ts)$/.test(f) && !f.endsWith('schema.d.ts') ? [readFileSync(p, 'utf8')] : []
  })
}

function flat(o: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(o).flatMap(([k, v]) =>
    v && typeof v === 'object' ? flat(v as Record<string, unknown>, `${prefix}${k}.`) : [`${prefix}${k}`],
  )
}

/** Keys read through a runtime prefix the scan cannot see (SettingField's i18n-base). */
const DYNAMIC = ['settings.device.', 'server.'] // server.* is built from the bridge's codes (i18n/server.ts)

describe('i18n keys', () => {
  it('every English key is used by the app (delete dead ones; add a prefix above for runtime-built keys)', () => {
    const src = sources(SRC).join('\n')
    const dynamic = new Set([
      ...[...src.matchAll(/[`'"]([\w.]+)\.\$\{/g)].map((m) => m[1]),
      ...[...src.matchAll(/['"`]([\w.]+)\.['"`]\s*\+/g)].map((m) => m[1]),
    ])
    const unused = flat(en as never).filter(
      (k) => !src.includes(k) && !DYNAMIC.some((p) => k.startsWith(p)) && ![...dynamic].some((p) => k.startsWith(`${p}.`)),
    )
    expect(unused).toEqual([])
  })
})
