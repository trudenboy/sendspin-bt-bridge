import { describe, it, expect } from 'vitest'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

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

  it('has the proposal, verification and done texts (with the administrator hint)', () => {
    const s = flatten((en as { sync: unknown }).sync)
    for (const k of ['proposal.forbiddenHint', 'wizard.verifying', 'wizard.memberGone', 'readOnly', 'done.restored', 'done.listen'])
      expect(s[k], k).toBeTruthy()
    expect(String(s['proposal.forbiddenHint'])).toContain('administrator')
    expect(String(flatten((ru as { sync: unknown }).sync)['proposal.forbiddenHint'])).toContain('администратор')
  })
})
