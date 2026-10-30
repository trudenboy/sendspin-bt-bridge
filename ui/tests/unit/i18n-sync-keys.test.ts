import { describe, it, expect } from 'vitest'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'

const flatten = (o: unknown, prefix = ''): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(o as Record<string, unknown>).flatMap(([k, v]) =>
      typeof v === 'object' && v !== null ? Object.entries(flatten(v, `${prefix}${k}.`)) : [[`${prefix}${k}`, v]],
    ),
  )

describe('sync texts', () => {
  it('has every sync key in English and Russian', () => {
    const enKeys = Object.keys(flatten((en as { sync: unknown }).sync)).sort()
    const ruKeys = Object.keys(flatten((ru as { sync: unknown }).sync)).sort()
    expect(ruKeys).toEqual(enKeys)
  })

  it('has the tune, cancel and done texts (with the administrator hint)', () => {
    const s = flatten((en as { sync: unknown }).sync)
    for (const k of ['proposal.forbiddenHint', 'tune.finish', 'cancel.message', 'wizard.memberGone', 'readOnly', 'done.restored', 'done.listen'])
      expect(s[k], k).toBeTruthy()
    expect(String(s['proposal.forbiddenHint'])).toContain('administrator')
    expect(String(flatten((ru as { sync: unknown }).sync)['proposal.forbiddenHint'])).toContain('администратор')
  })

  it('speaks of clicks and the click track only (no clicker, no test signal) in both languages', () => {
    const enText = Object.values(flatten((en as { sync: unknown }).sync)).join('\n').toLowerCase()
    const ruText = Object.values(flatten((ru as { sync: unknown }).sync)).join('\n').toLowerCase()
    for (const word of ['clicker', 'test signal', 'automatic', 'undo']) expect(enText, word).not.toContain(word)
    for (const word of ['кликер', 'тестовый сигнал', 'дорожка щелчков']) expect(ruText, word).not.toContain(word)
  })

  it('explains the sign of a delay the same way everywhere', () => {
    const s = flatten((en as { sync: unknown }).sync)
    expect(String(s['sign'])).toBe('+ plays earlier · − plays later')
    expect(String(s['manual.help'])).toContain('larger value makes a speaker play earlier')
    expect(String(s['delayHeading'])).toContain('larger = earlier')
  })
})

describe('playout texts', () => {
  const keys = [
    'device.chips.sync.playout', 'device.chips.sync.playoutTitle', 'device.chips.sync.settling', 'device.chips.sync.settlingTitle', 'device.chips.sync.jumpsTitle',
    'timing.playout.title', 'timing.playout.fallback', 'timing.playout.rows.error', 'timing.playout.rows.clockSlew', 'timing.playout.rows.warm',
  ]
  it('has the playout chip and timing keys in English and Russian', () => {
    for (const [name, lang] of [['en', en], ['ru', ru]] as const) {
      const flat = flatten(lang)
      for (const k of keys) expect(flat[k], `${name}:${k}`).toBeTruthy()
    }
  })
})
