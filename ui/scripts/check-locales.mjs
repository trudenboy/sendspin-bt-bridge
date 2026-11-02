#!/usr/bin/env node
/**
 * Consistency check for ui/src/i18n/locales/<lang>.json (en is the source).
 *
 * - missing keys: reported for every locale; they fall back to English at runtime,
 *   so only ru (the maintained translation) must be complete (exit code 1).
 *   ``--strict`` makes every locale that has a file complete too (used when
 *   accepting a fresh translation seed, not in CI: new English keys must not
 *   block releases until every community translation catches up).
 * - empty values are errors: vue-i18n treats an empty string as a translation,
 *   so it hides the English fallback.
 * - placeholder and plural mismatches break a sentence at runtime: always errors.
 *   Placeholders are checked per plural form, not only across the whole message:
 *   the aggregate variants use {devices}/{count} only in their second form.
 * - keys English no longer has (stale): reported, error for ru.
 *
 * Usage: node scripts/check-locales.mjs [--strict]   (npm run check:locales)
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

/** The pipe-separated plural forms of a message. */
export const formTexts = (text) => String(text).split('|').map((part) => part.trim())
const forms = (text) => formTexts(text).length
const isEmpty = (text) => String(text).trim() === ''

export function loadLocales(dir = LOCALES_DIR) {
  return Object.fromEntries(
    readdirSync(dir)
      .filter((f) => f.endsWith('.json'))
      .map((f) => [f.slice(0, -5), JSON.parse(readFileSync(join(dir, f), 'utf8'))]),
  )
}

export function checkLocales(messages = loadLocales()) {
  const en = flatten(messages.en)
  const total = Object.keys(en).length
  const report = {
    locales: Object.keys(messages),
    missing: {},
    extra: {},
    empty: [],
    placeholderMismatches: [],
    formPlaceholderMismatches: [],
    pluralMismatches: [],
    coverage: {},
  }
  for (const [locale, tree] of Object.entries(messages)) {
    if (locale === 'en') continue
    const flat = flatten(tree)
    report.missing[locale] = Object.keys(en).filter((k) => !(k in flat))
    report.extra[locale] = Object.keys(flat).filter((k) => !(k in en))
    const translated = Object.keys(en).filter((k) => k in flat && !isEmpty(flat[k])).length
    report.coverage[locale] = {
      translated,
      total,
      percent: total > 0 ? Math.round((translated / total) * 100) : 100,
    }
    for (const [key, text] of Object.entries(flat)) {
      if (!(key in en)) continue
      if (isEmpty(text)) report.empty.push({ locale, key })
      const want = placeholders(en[key])
      const got = placeholders(text)
      if (want.join() !== got.join()) report.placeholderMismatches.push({ locale, key, en: want, got })

      // Every English form's placeholders must survive in the matching target form.
      // A locale may add forms (ru has three); those must keep the last English
      // form's placeholders, because they are the aggregate variant.
      const enForms = formTexts(en[key])
      const gotForms = formTexts(text)
      enForms.forEach((enForm, index) => {
        const gotForm = gotForms[Math.min(index, gotForms.length - 1)] ?? ''
        const missing = placeholders(enForm).filter((p) => !placeholders(gotForm).includes(p))
        if (missing.length) report.formPlaceholderMismatches.push({ locale, key, form: index, missing })
      })
      for (let index = enForms.length; index < gotForms.length; index += 1) {
        const last = formTexts(en[key])[enForms.length - 1] ?? ''
        const missing = placeholders(last).filter((p) => !placeholders(gotForms[index]).includes(p))
        if (missing.length) report.formPlaceholderMismatches.push({ locale, key, form: index, missing })
      }

      if (forms(en[key]) > 1 && forms(text) < 2) report.pluralMismatches.push({ locale, key })
    }
  }
  return report
}

function main() {
  const strict = process.argv.includes('--strict')
  const r = checkLocales()
  let failed = false
  for (const locale of r.locales.filter((l) => l !== 'en')) {
    const required = REQUIRED.includes(locale) || strict
    const missing = r.missing[locale]
    const extra = r.extra[locale]
    const { translated, total, percent } = r.coverage[locale]
    console.log(
      `${locale}: ${translated}/${total} (${percent}%), ${missing.length} missing, ${extra.length} stale${required ? ' (must be complete)' : ' (falls back to en)'}`,
    )
    for (const k of missing.slice(0, 20)) console.log(`  missing ${k}`)
    for (const k of extra.slice(0, 20)) console.log(`  stale   ${k}`)
    if (required && (missing.length || extra.length)) failed = true
  }
  for (const e of r.empty) console.log(`empty ${e.locale}:${e.key}`)
  for (const m of r.placeholderMismatches) console.log(`placeholders ${m.locale}:${m.key} en={${m.en}} got={${m.got}}`)
  for (const m of r.formPlaceholderMismatches)
    console.log(`form placeholders ${m.locale}:${m.key}#${m.form} missing={${m.missing}}`)
  for (const m of r.pluralMismatches) console.log(`plural forms ${m.locale}:${m.key}`)
  if (
    r.empty.length ||
    r.placeholderMismatches.length ||
    r.formPlaceholderMismatches.length ||
    r.pluralMismatches.length
  )
    failed = true
  if (strict) console.log('strict: every locale with a file must be complete')
  process.exit(failed ? 1 : 0)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
