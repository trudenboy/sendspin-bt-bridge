import { describe, it, expect } from 'vitest'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import { ALL_FIELDS, SECTIONS } from '@/settings/layout'

const get = (m: unknown, path: string) => path.split('.').reduce<any>((o, k) => o?.[k], m)

describe('EXPERIMENTAL_AIR_SENSOR setting', () => {
  it('is an advanced toggle in the audio section', () => {
    const f = ALL_FIELDS.find((x) => x.key === 'EXPERIMENTAL_AIR_SENSOR')!
    expect(f.kind).toBe('toggle')
    expect(f.advanced).toBe(true)
    expect(SECTIONS.find((s) => s.id === 'audio')!.fields).toContain(f)
  })

  it.each([['en', en], ['ru', ru]])('has label and help in %s mentioning the requirements', (_l, m) => {
    expect(get(m, 'settings.fields.EXPERIMENTAL_AIR_SENSOR.label')).toBeTruthy()
    const help = get(m, 'settings.fields.EXPERIMENTAL_AIR_SENSOR.help') as string
    expect(help).toMatch(/CAP_NET_RAW/)
    expect(help).toMatch(/SBC/)
  })
})
