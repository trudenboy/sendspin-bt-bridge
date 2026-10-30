import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'

// Regression guard (UX stage 8): the web UI pages of the docs site (EN and RU)
// speak the words of docs/ux-glossary.md and name only controls that exist.

const ROOT = join(__dirname, '../../..')
const read = (rel: string) => readFileSync(join(ROOT, rel), 'utf8')
const PAGES = { en: 'docs-site/src/content/docs/web-ui.mdx', ru: 'docs-site/src/content/docs/ru/web-ui.mdx' } as const

type Tree = { [k: string]: string | Tree }
function strings(tree: Tree): string[] {
  return Object.values(tree).flatMap((v) => (typeof v === 'string' ? [v] : strings(v)))
}

/** Retired terms (docs/ux-glossary.md "Do not use"), matched the way the UI lint does. */
const RETIRED: Record<'en' | 'ru', Array<[RegExp, string]>> = {
  en: [
    [/\bhand(ed)?[- ]over\b|\btake back\b/i, 'Hand over / Take back -> Release / Reclaim'],
    [/\bclaim audio\b/i, 'Claim audio -> Use this bridge as the source'],
    [/\btransfer\b/i, 'Transfer -> Hand-off'],
    [/\bre-?anchor/i, 'Re-anchor -> Resync'],
    [/\bspeaker delay\b|\bstatic delay\b/i, 'Speaker delay -> Speaker latency compensation'],
    [/\badvanced mode\b|\bexpert mode\b|\bdeveloper mode\b/i, 'Advanced mode -> Show technical details'],
    [/\bsinks?\b/i, 'Sink -> Audio output'],
  ],
  ru: [
    [/отпуск|отпущ/i, 'Отпустить -> Освободить / Вернуть'],
    [/передат?ь|передан|забрать обратно/i, 'Передать -> Освободить / Вернуть'],
    [/забрать звук/i, 'Забрать звук -> Использовать этот мост'],
    [/подстро[йе]к/i, 'Подстройка -> Пересинхронизация'],
    [/статическая задержка|задержка колонки/i, 'Задержка колонки -> Компенсация задержки колонки'],
    [/расширенный режим|режим эксперта/i, 'Расширенный режим -> Показывать технические подробности'],
  ],
}

/** Bold phrases that are not interface labels (config keys, protocol names, section words). */
const NOT_A_LABEL = /^(`.*`|[A-Z][A-Z0-9_]+|Home Assistant|Music Assistant|Password|Пароль|Security|Безопасность|MFA|HTTPS|Sendspin|Speaker|Audio output|Player|Колонка|Аудиовыход|Плеер|Bluetooth)$/

function labels(text: string): string[] {
  // The sign-in section quotes the sign-in screen and its policy numbers, not Settings controls.
  const body = text
    .replace(/^---[\s\S]*?---/, '')
    .replace(/## (Signing in|Вход)\n[\s\S]*?(?=\n## )/, '')
    .replace(/^.*(HA Ingress).*$/m, '').replace(/```[\s\S]*?```/g, '')
  return [...new Set([...body.matchAll(/\*\*([^*\n]+)\*\*/g)].map((m) => m[1]!.trim()))]
}

const clean = (s: string) => s.toLowerCase().replace(/[«»"“”.…→]/g, '').replace(/\s+/g, ' ').trim()

describe('web UI docs', () => {
  for (const lang of ['en', 'ru'] as const) {
    const text = read(PAGES[lang])

    it(`${lang}: no retired glossary terms`, () => {
      const hits = RETIRED[lang].filter(([re]) => re.test(text)).map(([, hint]) => hint)
      expect(hits).toEqual([])
    })

    it(`${lang}: every bold control or state name exists in the interface text`, () => {
      const known = strings((lang === 'en' ? en : ru) as Tree).map(clean)
      // A label may be a part of a longer interface string ("Show technical details" in a menu item), never the reverse.
      const missing = labels(text).filter(
        (l) => !NOT_A_LABEL.test(l) && !l.includes('→ ') && !known.some((k) => k === clean(l) || k.includes(clean(l))),
      )
      expect(missing).toEqual([])
    })

    it(`${lang}: names the three connection states and the technical details preference`, () => {
      const tree = (lang === 'en' ? en : ru) as unknown as { status: { kind: Record<string, string> }; settings: { showAdvanced: string } }
      for (const name of Object.values(tree.status.kind)) expect(text).toContain(`**${name}**`)
      expect(text).toContain(tree.settings.showAdvanced)
    })
  }

  it('the glossary defines Technical details in both languages', () => {
    const glossary = read('docs/ux-glossary.md')
    expect(glossary).toContain('Show technical details')
    expect(glossary).toContain('Технические подробности')
    expect((en as unknown as { timing: { details: string } }).timing.details).toBe('Technical details')
    expect((ru as unknown as { timing: { details: string } }).timing.details).toBe('Технические подробности')
  })
})
