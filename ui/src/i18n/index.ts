import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import { frenchPlural, russianPlural } from './plural'
import { haLanguage } from '@/composables/haLanguage'

const STORAGE_KEY = 'sendspin-ui:locale'

/** One lazy loader per locale file: Vite emits each as its own chunk. English is bundled. */
const loaders = import.meta.glob<{ default: Record<string, unknown> }>(['./locales/*.json', '!./locales/en.json'])
const codeOf = (path: string) => path.slice('./locales/'.length, -'.json'.length)
const loaderFor = new Map(Object.entries(loaders).map(([path, load]) => [codeOf(path), load]))

/** The app's one i18n instance; stores use ``i18n.global.t`` outside components. */
export const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: { en },
  pluralRules: { ru: russianPlural, fr: frenchPlural },
})

/** Codes that mean a shipped file but are not the file's own spelling. */
const LOCALE_ALIASES: Record<string, string> = {
  zh: 'zh-Hans',
  'zh-cn': 'zh-Hans',
  'zh-sg': 'zh-Hans',
  'zh-hans': 'zh-Hans',
}

/** We ship Simplified Chinese only; never silently show it to a Traditional reader. */
const TRADITIONAL_CHINESE = /^zh-(hant|tw|hk|mo)(-|$)/

const normalizeTag = (tag: string) => tag.trim().toLowerCase().replace(/_/g, '-')

/** Locale codes with a file, English first, the rest by their own names. */
export function availableLocales(): string[] {
  const byName = new Intl.Collator('en')
  const rest = [...loaderFor.keys()].sort((a, b) => byName.compare(languageName(a), languageName(b)))
  return ['en', ...rest]
}

/**
 * ``ru-RU`` → ``ru``, ``zh-CN``/``zh-Hans`` → ``zh-Hans``; null when we have no
 * file for the language. ``zh-TW`` and ``zh-Hant`` stay null: we ship Simplified
 * Chinese only, and falling back to it would hand readers the wrong script.
 */
export function matchLocale(tag: string | null | undefined): string | null {
  const normalized = tag ? normalizeTag(tag) : ''
  if (!normalized) return null
  const locales = availableLocales()
  const exact = locales.find((code) => code.toLowerCase() === normalized)
  if (exact) return exact
  if (TRADITIONAL_CHINESE.test(normalized)) return null
  const alias = LOCALE_ALIASES[normalized] ?? LOCALE_ALIASES[normalized.split('-')[0] ?? '']
  if (alias && locales.includes(alias)) return alias
  const base = normalized.split('-')[0] ?? ''
  return locales.includes(base) ? base : null
}

/** Fetch a locale's messages once; false when there is no such locale. */
export async function loadLocale(code: string): Promise<boolean> {
  if (code === 'en') return true
  if ((i18n.global.availableLocales as string[]).includes(code)) return true
  const load = loaderFor.get(code)
  if (!load) return false
  try {
    i18n.global.setLocaleMessage(code, (await load()).default as never)
    return true
  } catch {
    return false // offline or a stale chunk: English keeps working
  }
}

/** Load (if needed) and switch to a locale; unknown or failed loads leave the current one. */
export async function setLocale(code: string): Promise<void> {
  if (await loadLocale(code)) i18n.global.locale.value = code as never
}

/** The language to start in: the user's saved choice, else Home Assistant's, else English. */
export function initialLocale(): string {
  try {
    const saved = matchLocale(localStorage.getItem(STORAGE_KEY))
    if (saved) return saved
  } catch {
    /* storage blocked: no saved choice */
  }
  return matchLocale(haLanguage()) ?? 'en'
}

/** Remember an explicit choice (a deliberate pick overrides Home Assistant's language from then on). */
export async function chooseLocale(code: string): Promise<void> {
  await setLocale(code)
  try {
    localStorage.setItem(STORAGE_KEY, code)
  } catch {
    /* remembered for this visit only */
  }
}

/** Apply the starting language before the app mounts, so there is no flash of English. */
export function bootLocale(): Promise<void> {
  return setLocale(initialLocale())
}

/** A language in its own words ("English", "Русский"), for the switcher. */
export function languageName(code: string): string {
  try {
    const name = new Intl.DisplayNames([code], { type: 'language' }).of(code) ?? code
    return name.charAt(0).toLocaleUpperCase(code) + name.slice(1)
  } catch {
    return code
  }
}
