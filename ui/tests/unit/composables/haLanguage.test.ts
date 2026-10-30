import { afterEach, describe, expect, it, vi } from 'vitest'
import { haLanguage } from '@/composables/haLanguage'

function fakeParent(opts: { lang?: string; stored?: string | null }) {
  return {
    document: { documentElement: { lang: opts.lang ?? '' } },
    localStorage: { getItem: (k: string) => (k === 'selectedLanguage' ? (opts.stored ?? null) : null) },
  } as unknown as Window
}

afterEach(() => vi.restoreAllMocks())

describe('haLanguage', () => {
  it('is null outside an iframe (no Home Assistant around us)', () => {
    expect(haLanguage()).toBeNull()
  })

  it('reads the language Home Assistant put on its page', () => {
    expect(haLanguage({ parent: fakeParent({ lang: 'ru' }), self: {} as Window })).toBe('ru')
  })

  it('falls back to the stored per-browser choice', () => {
    expect(haLanguage({ parent: fakeParent({ stored: '"de"' }), self: {} as Window })).toBe('de')
  })

  it('falls back to the browser language when the HA page is another origin', () => {
    const parent = {} as Window
    Object.defineProperty(parent, 'document', { get() { throw new DOMException('blocked') } })
    expect(haLanguage({ parent, self: {} as Window, navigatorLanguages: ['ru-RU', 'en'] })).toBe('ru-RU')
  })
})
