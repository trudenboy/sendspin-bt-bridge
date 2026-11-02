# Translating the web interface

The web interface is translated by the community: one JSON file per language,
English as the source, and [Weblate](https://hosted.weblate.org) as the review
tool on top of the files.

## Languages

| Code | Language | File | Status |
|---|---|---|---|
| `en` | English | `en.json` | source, always bundled |
| `ru` | Русский | `ru.json` | maintained reference, must be complete |
| `de` | Deutsch | `de.json` | community translation, reviewed on Weblate |
| `es` | Español | `es.json` | community translation, reviewed on Weblate |
| `fr` | Français | `fr.json` | community translation, reviewed on Weblate |
| `it` | Italiano | `it.json` | community translation, reviewed on Weblate |
| `ja` | 日本語 | `ja.json` | community translation, reviewed on Weblate |
| `ko` | 한국어 | `ko.json` | community translation, reviewed on Weblate |
| `tr` | Türkçe | `tr.json` | community translation, reviewed on Weblate |
| `zh-Hans` | 简体中文 | `zh-Hans.json` | community translation, reviewed on Weblate |

Traditional Chinese (`zh-Hant`, `zh-TW`) is not shipped: the browser and Home
Assistant tags for it deliberately fall back to English rather than showing the
wrong script. Add it later as its own file.

## Layout

```
ui/src/i18n/locales/
  en.json       source language, always bundled, the fallback for everything
  ru.json       maintained reference translation, must be complete
  <lang>.json   any new language: drop the file in, nothing else to register
```

- Keys are nested (`header.language`); placeholders are `{name}`.
- Plurals are pipe-separated (`one | many`); Russian has three forms
  (`one | few | many`), the other languages have two.
- Some two-part strings are not grammar: the first part is for one speaker, the
  second is the aggregate variant for several and uses `{devices}` or `{count}`.
  Keep both parts and their placeholders; see the rule below.
- Only the selected language is downloaded. `en` is part of the main bundle,
  every other file is its own lazy chunk (checked after each `npm run build`).
- A missing key in any language except `ru` falls back to English at runtime.
  `ru` must have every English key and no stale ones.
- A language is offered in Preferences as soon as its file exists; its name is
  shown in its own language, English first.

## Checks

```bash
cd ui
npm run check:locales          # missing/stale keys, empty values, placeholders, forms
npm run check:locales:strict   # every locale that exists must be complete (seed/release only)
npm test                       # the same checks run in tests/unit/i18n/locales.test.ts
```

The checker validates placeholders **per plural form**, not only across the whole
message, so a translation that loses `{devices}` from the aggregate part fails.
Empty values are errors: vue-i18n treats `""` as a translation and would hide the
English fallback. Missing keys are reported for every language and fail only for
`ru` (and for everyone under `--strict`).

## Plural and tone rules

| Language | Rule |
|---|---|
| en, de, es, it | one / other; 0 uses the plural |
| fr | 0 and 1 are singular (`0 correction`), 2+ plural |
| ru | one / few / many |
| tr | after a numeral the noun stays singular (`3 cihaz`), so real count plurals repeat the singular; aggregate variants differ |
| ja, ko, zh-Hans | no grammatical plural: real count plurals repeat the same wording in both parts; aggregate variants differ |
| all | the aggregate part keeps `{devices}`/`{count}`; never merge the two parts |

Tone (matching Music Assistant): de `du`, es `tú`, fr `vous`, it `tu`, tr neutral,
ja `です・ます`, ko `-습니다`, zh-Hans neutral, ru `вы`.

Product and protocol names stay in Latin script: Music Assistant, Sendspin,
Bluetooth, Home Assistant, Docker, PulseAudio, PipeWire, A2DP, SBC, AAC, LDAC.
Configuration keys (`SENDSPIN_PORT`), code identifiers and the literal `<token>`
are not translated. The shared term table is
[`docs/i18n/glossary.csv`](i18n/glossary.csv); read
[`docs/i18n/translation-brief.md`](i18n/translation-brief.md) before a large pass.

## Weblate

The first seed is machine-assisted. Once the hosted project is live, the seed is
marked **Needs editing** and the community review turns it into a maintained
translation. The project settings are:

| Setting | Value |
|---|---|
| Project / component | `sendspin-bt-bridge` / `web-ui` |
| Repository | `https://github.com/trudenboy/sendspin-bt-bridge` |
| Branch | `beta` |
| File mask | `ui/src/i18n/locales/*.json` |
| Monolingual base | `ui/src/i18n/locales/en.json` |
| File format | JSON nested structure file |
| Language code style | BCP 47 hyphen (`zh-Hans`, not `zh_Hans`) |
| Template for new translations | empty (do not copy English: it would count as translated) |
| Language filter | `^(?!ru$).+$` — Russian stays maintained in the repository |
| Push | GitHub App, push branch `weblate-web-ui`, pull request into `beta` |
| License | MIT |

Add-ons: **Cleanup translation files** (`weblate.cleanup.generic`) and **Remove
blank strings** (`weblate.cleanup.blank`). Automatic translation is optional and
needs a configured machine-translation engine.

Translation instructions for the component:

- Keep `{placeholders}` exactly; keep product names and protocol terms.
- Strings with `|` have two parts: one speaker / several speakers. Keep both.
  For ja/ko/zh-Hans repeat the wording for real count plurals; in tr the noun
  stays singular after a number. The aggregate part keeps `{devices}`/`{count}`.
- The seed is machine-assisted: correct wording, terms and grammar freely.
- Tone: de `du`, es `tú`, fr `vous`, it `tu`, tr neutral, ja `です・ます`,
  ko `-습니다`, zh-Hans neutral.

A translation change reaches `beta` as a pull request and must pass CI
(`npm run check:locales`, build and tests). Do not edit `en.json` in a
translation PR; source wording is a separate change.

The one-time setup, exact component fields, the seed review pass and the
first-PR check are in [`docs/i18n/weblate-setup.md`](i18n/weblate-setup.md).

## Adding a language

1. Copy `en.json` to `<lang>.json`.
2. Translate the values; keep placeholders, pipe parts and product names.
3. `cd ui && npm run check:locales -- <lang>` and `npm run build`.
4. Add the language to `matchLocale()` if it needs a region/script alias, and to
   the tests in `ui/tests/unit/i18n/lazy.test.ts`.
5. Open a pull request. Weblate discovers the new file on the next pull.

## Language in Home Assistant

Outside Home Assistant the interface starts in English. Inside the add-on panel,
when the person has not picked a language in Preferences, it starts in Home
Assistant's language, read in this order:

1. `<html lang>` of the Home Assistant page around the panel (ingress shares its origin), which is what Home Assistant itself renders with, profile setting included.
2. Home Assistant's per-browser `selectedLanguage` entry.
3. The browser's own language, when the panel cannot read the Home Assistant page.

Tags are normalized (`zh-CN` → `zh-Hans`, `de-DE` → `de`); `zh-TW`/`zh-Hant`
stay English until a Traditional Chinese file exists. Choosing a language in
Preferences always wins afterwards.
