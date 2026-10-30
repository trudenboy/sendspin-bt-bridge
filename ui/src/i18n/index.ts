import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import { russianPlural } from './plural'
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
  pluralRules: { ru: russianPlural },
})

/** Locale codes with a file, English (the source and fallback) first. */
export function availableLocales(): string[] {
  return ['en', ...[...loaderFor.keys()].sort()]
}

/** ``ru-RU`` → ``ru``; null when we have no file for the language. */
export function matchLocale(tag: string | null | undefined): string | null {
  const base = tag?.toLowerCase().split(/[-_]/)[0]
  return base && availableLocales().includes(base) ? base : null
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
