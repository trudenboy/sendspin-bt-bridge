#!/usr/bin/env node
// Build check: every non-English locale must be its own lazy chunk, and something loads it.
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const assets = join(root, 'dist/assets')
const locales = readdirSync(join(root, 'src/i18n/locales')).map((f) => f.slice(0, -5)).filter((l) => l !== 'en')
const files = readdirSync(assets).filter((f) => f.endsWith('.js'))
let failed = false
for (const code of locales) {
  const chunk = files.find((f) => new RegExp(`^${code}-[\\w-]+\\.js$`).test(f))
  const entry = files.filter((f) => !f.startsWith(`${code}-`)).map((f) => readFileSync(join(assets, f), 'utf8')).join('\n')
  if (!chunk) {
    console.error(`locale ${code}: no separate chunk in dist/assets`)
    failed = true
  } else if (!entry.includes(chunk)) {
    console.error(`locale ${code}: chunk ${chunk} is never referenced (not lazy-loaded)`)
    failed = true
  }
}
if (failed) process.exit(1)
console.log(`locale chunks ok: ${locales.join(', ')}`)
