#!/usr/bin/env node
/**
 * Consistency check for ui/src/i18n/locales/<lang>.json (en is the source).
 *
 * - missing keys: reported for every locale; they fall back to English at runtime,
 *   so only ru (the maintained translation) must be complete (exit code 1).
 * - placeholder and plural mismatches break a sentence at runtime: always errors.
 * - keys English no longer has (stale): reported, error for ru.
 *
 * Usage: node scripts/check-locales.mjs   (npm run check:locales)
 */
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const LOCALES_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../src/i18n/locales')
/** Locales that must be complete; the others may lag behind English. */
const REQUIRED = ['ru']

export function flatten(o, prefix = '') {
  return Object.fromEntries(
    Object.entries(o).flatMap(([k, v]) =>
      v && typeof v === 'object' ? Object.entries(flatten(v, `${prefix}${k}.`)) : [[`${prefix}${k}`, v]],
    ),
  )
}

/** Sorted unique {name} / {0} placeholders of a message. */
export function placeholders(text) {
  return [...new Set([...String(text).matchAll(/\{\s*([\w.]+)\s*\}/g)].map((m) => m[1]))].sort()
}

const forms = (text) => String(text).split('|').length

export function loadLocales(dir = LOCALES_DIR) {
  return Object.fromEntries(
    readdirSync(dir)
      .filter((f) => f.endsWith('.json'))
      .map((f) => [f.slice(0, -5), JSON.parse(readFileSync(join(dir, f), 'utf8'))]),
  )
}

export function checkLocales(messages = loadLocales()) {
  const en = flatten(messages.en)
  const report = { locales: Object.keys(messages), missing: {}, extra: {}, placeholderMismatches: [], pluralMismatches: [] }
  for (const [locale, tree] of Object.entries(messages)) {
    if (locale === 'en') continue
    const flat = flatten(tree)
    report.missing[locale] = Object.keys(en).filter((k) => !(k in flat))
    report.extra[locale] = Object.keys(flat).filter((k) => !(k in en))
    for (const [key, text] of Object.entries(flat)) {
      if (!(key in en)) continue
      const want = placeholders(en[key])
      const got = placeholders(text)
      if (want.join() !== got.join()) report.placeholderMismatches.push({ locale, key, en: want, got })
      if (forms(en[key]) > 1 && forms(text) < 2) report.pluralMismatches.push({ locale, key })
    }
  }
  return report
}

function main() {
  const r = checkLocales()
  let failed = false
  for (const locale of r.locales.filter((l) => l !== 'en')) {
    const required = REQUIRED.includes(locale)
    const missing = r.missing[locale]
    const extra = r.extra[locale]
    console.log(`${locale}: ${missing.length} missing, ${extra.length} stale${required ? ' (must be complete)' : ' (falls back to en)'}`)
    for (const k of missing.slice(0, 20)) console.log(`  missing ${k}`)
    for (const k of extra.slice(0, 20)) console.log(`  stale   ${k}`)
    if (required && (missing.length || extra.length)) failed = true
  }
  for (const m of r.placeholderMismatches) console.log(`placeholders ${m.locale}:${m.key} en={${m.en}} got={${m.got}}`)
  for (const m of r.pluralMismatches) console.log(`plural forms ${m.locale}:${m.key}`)
  if (r.placeholderMismatches.length || r.pluralMismatches.length) failed = true
  process.exit(failed ? 1 : 0)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
