import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

/**
 * UX audit M4: the user guide quotes the UI. Every bold label in a guide must be a text the UI shows
 * in that language (a "…" in the guide stands for a name), apart from the few listed here that are
 * not UI labels (headings of the guide, product names) or come from the phone capture page.
 */
const NOT_UI: Record<'en' | 'ru', string[]> = {
  en: ['Home Assistant ingress', 'Sendspin sync clicks', 'Music Assistant tab', 'Done.', 'Device card or row',
    'Or choose a recording', 'Record with the phone', 'A larger delay makes a speaker play earlier.', 'Prepare',
    'Measure: Speaker by speaker.', 'Tune: Listen and adjust.', 'Sync', 'Play the sequence'],
  ru: ['Ingress Home Assistant', 'Sendspin sync clicks', 'Вкладка Music Assistant', 'Готово.', 'Карточка или строка устройства',
    'Или выберите запись', 'Записать телефоном', 'Проиграть последовательность', 'Чем больше задержка, тем раньше звучит колонка.',
    'Подготовка', 'Измерение: Колонка за колонкой.', 'Настройка: Послушайте и подправьте.'],
}

function texts(o: unknown): string[] {
  return Object.values(o as Record<string, unknown>).flatMap((v) =>
    typeof v === 'object' && v !== null ? texts(v) : String(v).split('|').map((s) => s.trim()),
  )
}
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function missing(lang: 'en' | 'ru', file: string, messages: unknown): string[] {
  const doc = readFileSync(resolve(__dirname, '../../../docs-site/src/content/docs', file), 'utf8')
  const ui = texts(messages)
  const bolds = [...new Set([...doc.matchAll(/\*\*(.+?)\*\*/g)].map((m) => m[1]!))]
  return bolds.filter((b) => {
    if (NOT_UI[lang].includes(b)) return false
    const wanted = new RegExp(`^${escape(b).replace(/…/g, '.+')}$`)
    return !ui.some((t) => t === b || wanted.test(t.replace(/\{\w+\}/g, 'X')) || new RegExp(`^${escape(t).replace(/\\\{\w+\\\}/g, '.+')}$`).test(b))
  })
}

describe('the group sync guides quote the UI', () => {
  it('Russian guide labels exist in ru.json', () => {
    expect(missing('ru', 'ru/group-sync-tuning.md', ru)).toEqual([])
  })
  it('English guide labels exist in en.json', () => {
    expect(missing('en', 'group-sync-tuning.md', en)).toEqual([])
  })
})
