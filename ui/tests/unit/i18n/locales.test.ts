import { describe, expect, it } from 'vitest'
// @ts-expect-error plain ESM script shared with `npm run check:locales`
import { checkLocales, placeholders, flatten } from '../../../scripts/check-locales.mjs'

describe('locale files', () => {
  const report = checkLocales()

  it('finds en as the source and ru next to it', () => {
    expect(report.locales).toEqual(expect.arrayContaining(['en', 'ru']))
  })

  it('has every English key in Russian (ru must be complete)', () => {
    expect(report.missing.ru ?? []).toEqual([])
  })

  it('keeps placeholders identical to English in every locale', () => {
    expect(report.placeholderMismatches).toEqual([])
  })

  it('keeps plural forms where English has them', () => {
    expect(report.pluralMismatches).toEqual([])
  })

  it('has no keys that English does not know (stale)', () => {
    expect(report.extra.ru ?? []).toEqual([])
  })
})

describe('checker', () => {
  it('reads {named} and {0} placeholders', () => {
    expect(placeholders('Hi {name}, {n} left, {0}')).toEqual(['0', 'n', 'name'])
  })
  it('flattens nested messages', () => {
    expect(flatten({ a: { b: 'x' }, c: 'y' })).toEqual({ 'a.b': 'x', c: 'y' })
  })
  it('flags a missing key, a lost placeholder and a lost plural', () => {
    const r = checkLocales({ en: { a: 'x {n}', b: 'one | many', c: 'z' }, de: { a: 'x', b: 'eins' } })
    expect(r.missing.de).toEqual(['c'])
    expect(r.placeholderMismatches).toEqual([{ locale: 'de', key: 'a', en: ['n'], got: [] }])
    expect(r.pluralMismatches).toEqual([{ locale: 'de', key: 'b' }])
  })
})
