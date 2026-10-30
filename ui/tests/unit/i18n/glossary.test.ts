import { describe, expect, it } from 'vitest'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'

// Enforces docs/ux-glossary.md: one term per concept, retired terms stay out of
// user-facing strings. Technical keys that legitimately keep a word (a software
// release, a release candidate) are listed explicitly.

type Tree = { [k: string]: string | Tree }

function flatten(tree: Tree, prefix = ''): Array<[string, string]> {
  return Object.entries(tree).flatMap(([k, v]) =>
    typeof v === 'string' ? [[prefix + k, v] as [string, string]] : flatten(v, `${prefix}${k}.`),
  )
}

/** Keys (or key prefixes ending in ".") where a retired word is a real, different concept. */
const ALLOWLIST = [
  'update.', // software "Release Notes"
  'settings.fields.UPDATE_CHANNEL.', // "Release candidate"
  'server.cause.module_suspend_on_idle.', // names the PulseAudio module
  'server.cause.wireplumber_suspend_timeout.', // names the WirePlumber option
]

const allowed = (key: string) => ALLOWLIST.some((p) => (p.endsWith('.') ? key.startsWith(p) : key === p))

const RETIRED: Record<'en' | 'ru', Array<[RegExp, string]>> = {
  en: [
    [/\bhand(ed)?[- ]over\b|\btake back\b/i, 'Hand over / Take back -> Release / Reclaim'],
    [/\bpause output\b|\bresume output\b/i, 'Pause output -> Power save'],
    [/\bclaim(s|ed)?\b/i, 'Claim audio -> Use this bridge as the source'],
    [/\btransfer\b/i, 'Transfer -> Hand-off'],
    [/\bre-?anchor/i, 'Re-anchor -> Resync'],
    [/\bsuspend(ed)?\b/i, 'Suspend -> Power save (the action) / Pause (the output)'],
    [/\bspeaker delay\b|\bstatic delay\b/i, 'Speaker delay -> Speaker latency compensation'],
  ],
  ru: [
    [/отпуск|отпущ/i, 'Отпустить -> Освободить / Вернуть'],
    [/передат?ь|передан|забрать обратно/i, 'Передать / Забрать обратно -> Освободить / Вернуть'],
    [/пауза вывода|вывод на паузу|возобновить вывод/i, 'Пауза вывода -> Энергосбережение'],
    [/забрать звук/i, 'Забрать звук -> Использовать этот мост'],
    [/передач[аиу]\b/i, 'Передача (chip) -> Передача между комнатами'],
    [/подстро[йе]к|подстройк/i, 'Подстройка -> Пересинхронизация'],
    [/приостанов/i, 'Приостановить -> Поставить вывод на паузу'],
    [/статическая задержка|задержка колонки/i, 'Задержка колонки -> Компенсация задержки колонки'],
  ],
}

const SYNONYM_PAIRS: Array<{ en: [RegExp, RegExp]; why: string }> = [
  { en: [/\bhand(ed)? over\b/i, /\breleas/i], why: 'Hand over vs Release' },
  { en: [/\bpower save\b/i, /\bsuspend/i], why: 'Power save vs Suspend' },
]

describe('ux glossary', () => {
  for (const [lang, tree] of [['en', en], ['ru', ru]] as const) {
    const entries = flatten(tree as Tree).filter(([k]) => !allowed(k))
    it(`${lang}: no retired terms in user-facing strings`, () => {
      const hits: string[] = []
      for (const [key, value] of entries)
        for (const [re, hint] of RETIRED[lang]) if (re.test(value)) hits.push(`${key}: "${value}" (${hint})`)
      expect(hits).toEqual([])
    })
  }

  it('en: no string mixes synonyms of one concept', () => {
    const hits: string[] = []
    for (const [key, value] of flatten(en as Tree).filter(([k]) => !allowed(k)))
      for (const { en: [a, b], why } of SYNONYM_PAIRS) if (a.test(value) && b.test(value)) hits.push(`${key}: ${why}`)
    expect(hits).toEqual([])
  })

  it('the release action has the same name on every surface', () => {
    expect((en as any).devices.bulk.release.label).toBe('Release')
    expect((en as any).speaker.handOver).toMatch(/^Release/)
    expect((en as any).bluetooth.release).toMatch(/^Release/)
    expect((ru as any).devices.bulk.release.label).toBe('Освободить')
    expect((ru as any).speaker.handOver).toMatch(/^Освободить/)
    expect((en as any).speaker.takeBack).toMatch(/^Reclaim/)
    expect((ru as any).speaker.takeBack).toMatch(/^Вернуть/)
  })

  it('power save has the same name on every surface', () => {
    expect((en as any).devices.bulk.power_save.label).toBe('Power save')
    expect((ru as any).devices.bulk.power_save.label).toBe('Энергосбережение')
    expect((en as any).settings.device.idle_mode.options.power_save).toBe('Power save')
    expect((ru as any).settings.device.idle_mode.options.power_save).toBe('Энергосбережение')
  })

  it('both languages stay in step (same keys)', () => {
    const k = (t: unknown) => flatten(t as Tree).map(([key]) => key).sort()
    expect(k(ru)).toEqual(k(en))
  })
})
