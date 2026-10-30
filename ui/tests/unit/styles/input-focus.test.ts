import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? files(p) : /\.vue$/.test(f) ? [p] : []
  })
}

describe('text inputs use the global focus outline', () => {
  it('no component swaps it for a faint focus:ring-primary', () => {
    const offenders = files(resolve(__dirname, '../../../src')).filter((f) => /focus:ring-(2|1)?\s|focus:ring-primary/.test(readFileSync(f, 'utf8')))
    expect(offenders).toEqual([])
  })
  it('no input suppresses the outline with outline-none', () => {
    const offenders = files(resolve(__dirname, '../../../src')).filter((f) =>
      readFileSync(f, 'utf8')
        .split('\n')
        .some((l) => /outline-none/.test(l) && !/tabindex|after:ring/.test(l) && !/SbInput/.test(f) ),
    )
    expect(offenders).toEqual([])
  })
})
