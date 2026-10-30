# Translating the web interface

The web interface is translated by the community, the same way Music Assistant does it: one JSON file per language, English as the source, and a hosted translation service on top of the files.

## Layout

```
ui/src/i18n/locales/
  en.json   source language, always bundled, the fallback for everything
  ru.json   maintained translation, must be complete
  <lang>.json   any new language: drop the file in, nothing else to register
```

- Keys are nested (`header.language`); placeholders are `{name}`; plurals use vue-i18n's `one | other` (Russian has three forms: `one | few | many`).
- Only the selected language is downloaded. `en` is part of the main bundle, every other file is its own lazy chunk (checked after each `npm run build`).
- A missing key in any language except `ru` falls back to English at runtime. `ru` must have every English key and no stale ones.
- A language is offered in Preferences as soon as its file exists; its name is shown in its own language.

## Checks

```bash
cd ui
npm run check:locales   # missing/stale keys, placeholders, plural forms
npm test                # the same checks run in tests/unit/i18n/locales.test.ts
```

Placeholder and plural mismatches fail for every language (they break a sentence at runtime). Missing keys are reported for every language and fail only for `ru`.

## Weblate (recommended host)

[Weblate](https://hosted.weblate.org) hosts libre projects for free. Setup, done once by a maintainer:

1. Create the project and one component from the GitHub repository.
2. File mask `ui/src/i18n/locales/*.json`, base file `ui/src/i18n/locales/en.json`, file format "JSON nested structure file".
3. Template for new languages: `ui/src/i18n/locales/en.json`; new translations are `ru`-complete only when translators finish them.
4. Enable the "Weblate will push changes" pull request flow (not direct pushes), so every translation update arrives as a normal PR that CI checks.
5. Add the Weblate webhook to the repository so English changes reach translators immediately.

Crowdin works the same way with the same mask. No account or integration is configured by this repository.

## Without Weblate

Copy `en.json` to `<lang>.json`, translate the values (keep `{placeholders}` and the number of `|` plural forms), run `npm run check:locales`, and open a pull request. Do not edit `en.json` wording in a translation PR.

## Language in Home Assistant

Outside Home Assistant the interface starts in English. Inside the add-on panel, when the person has not picked a language in Preferences, it starts in Home Assistant's language, read in this order:

1. `<html lang>` of the Home Assistant page around the panel (ingress shares its origin), which is what Home Assistant itself renders with, profile setting included.
2. Home Assistant's per-browser `selectedLanguage` entry.
3. The browser's own language, when the panel cannot read the Home Assistant page.

Limits: ingress passes the user's id and name but not their language, so the server cannot know it; a browser whose language differs from the HA profile may land on the browser language in case 3. A language without a file falls back to English. Choosing a language in Preferences always wins afterwards.
