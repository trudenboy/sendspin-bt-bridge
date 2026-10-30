import { describe, expect, it, vi } from 'vitest'
import { i18n, loadLocale, setLocale, availableLocales, matchLocale } from '@/i18n'

describe('lazy locales', () => {
  it('bundles only English up front', async () => {
    vi.resetModules()
    const fresh = await import('@/i18n')
    expect(Object.keys(fresh.i18n.global.messages.value)).toEqual(['en'])
  })

  it('lists the locale files it can load, English first', () => {
    expect(availableLocales()[0]).toBe('en')
    expect(availableLocales()).toContain('ru')
  })

  it('loads Russian on demand and switches to it', async () => {
    await setLocale('ru')
    expect(i18n.global.locale.value).toBe('ru')
    expect(Object.keys(i18n.global.messages.value)).toContain('ru')
    expect(i18n.global.t('header.language')).not.toBe('Language')
    await setLocale('en')
    expect(i18n.global.t('header.language')).toBe('Language')
  })

  it('ignores a locale it has no file for', async () => {
    await setLocale('xx')
    expect(i18n.global.locale.value).toBe('en')
    expect(await loadLocale('xx')).toBe(false)
  })

  it('maps browser tags to a supported locale', () => {
    expect(matchLocale('ru-RU')).toBe('ru')
    expect(matchLocale('RU')).toBe('ru')
    expect(matchLocale('en-GB')).toBe('en')
    expect(matchLocale('xx')).toBeNull()
    expect(matchLocale(null)).toBeNull()
  })
})
