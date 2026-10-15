import { describe, it, expect, afterEach } from 'vitest'
import { i18n } from '@/i18n'

const t = i18n.global.t

describe('profile skipped count plural', () => {
  afterEach(() => { i18n.global.locale.value = 'en' })

  it('uses one/other in English', () => {
    i18n.global.locale.value = 'en'
    expect(t('sync.profiles.skipped', 1, { named: { n: 1 } })).toBe('1 member skipped')
    expect(t('sync.profiles.skipped', 3, { named: { n: 3 } })).toBe('3 members skipped')
  })

  it('uses the three Russian forms', () => {
    i18n.global.locale.value = 'ru'
    const say = (n: number) => t('sync.profiles.skipped', n, { named: { n } }).replace(String(n), '#')
    expect(say(1)).toBe(say(21))
    expect(say(2)).toBe(say(4))
    expect(say(2)).toBe(say(22))
    expect(say(5)).toBe(say(11))
    expect(say(5)).toBe(say(12))
    expect(say(5)).toBe(say(0))
    expect(new Set([say(1), say(2), say(5)]).size).toBe(3)
    expect(t('sync.profiles.skipped', 21, { named: { n: 21 } })).toContain('21')
  })
})
