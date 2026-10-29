import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? files(p) : /\.(vue|ts)$/.test(f) ? [p] : []
  })
}

describe('warning colour as text', () => {
  it('no component uses the decorative text-warning colour (1.9:1 contrast)', () => {
    const offenders = files(resolve(__dirname, '../../../src')).filter((f) =>
      /(?<![\w-])text-warning(?![\w-])/.test(readFileSync(f, 'utf8')),
    )
    expect(offenders).toEqual([])
  })
})
