# Translation brief (first seed)

This file guides the first machine-assisted seed of the eight new languages.
The source of truth is always `ui/src/i18n/locales/en.json`; `ru.json` is the
reference translation and is not edited by the seed.

## Tone

Use one voice per language, matching what Music Assistant already does:

| Language | Address the reader as | Notes |
|---|---|---|
| de | `du` | Verb first in short actions; avoid `Sie`. |
| es | `tú` / `tu` | Neutral Spanish, not regional. |
| fr | `vous` | French UI convention; `votre`, `vos`. |
| it | `tu` | Short, direct actions. |
| tr | neutral / polite | No `siz` unless the English explicitly says "you"; numbers stay singular (`3 cihaz`). |
| ja | `です・ます` | Neutral polite; no pronouns where Japanese omits them. |
| ko | `-습니다` / `-세요` | Neutral polite; omit pronouns. |
| zh-Hans | neutral | No `您`/`你` unless needed; short verb-object labels. |
| ru | `вы` | Existing file; do not change it in this batch. |

## Non-negotiables

- Keep every `{placeholder}` exactly (`{n}`, `{device}`, `{devices}`, `{count}`,
  `{name}`, `{sink}`, `{port}`, …). Do not translate the name inside braces.
- Keep product and protocol names in Latin script: Music Assistant, Sendspin,
  Bluetooth, Home Assistant, Docker, PulseAudio, PipeWire, A2DP, SBC, AAC, LDAC,
  Wi-Fi, URL, IP, API.
- Keep configuration keys and code identifiers exactly as written:
  `SENDSPIN_PORT`, `POWER_SAVE_DELAY`, `hci0`, `bluez_sink.*`, paths, log levels.
- Keep the literal `<token>` in the token help text.
- No empty values. If the English is a bare product name, keep it.
- Preserve punctuation that carries meaning (`:`, `…`, `—` is not a sentence
  separator; use the language's own dash style).
- Use the glossary in `docs/i18n/glossary.csv`. If a term is missing, choose a
  consistent translation and keep it across every chunk.

## Plural strings (` | `)

Some English messages contain two variants separated by ` | `. They are not
always grammar:

- The first variant is for one speaker; the second is for several.
- Some second variants are aggregate diagnostics and use `{devices}` or
  `{count}` instead of `{device}`. Keep those placeholders in the second part.
- New languages keep **two** parts even when the language does not inflect:
  - ja / ko / zh-Hans: for real count plurals, repeat the same wording in both
    parts; for aggregate variants, translate the two variants differently.
  - tr: after a numeral the noun stays singular (`3 cihaz`), so real count
    plurals repeat the singular noun; aggregate variants differ.
  - fr: the first part is used for 0 and 1, so write it in the singular.
- Never drop a part and never merge the two variants into one sentence.

Examples:

```
EN  "{n} correction | {n} corrections"
DE  "{n} Korrektur | {n} Korrekturen"
FR  "{n} correction | {n} corrections"
TR  "{n} düzeltme | {n} düzeltme"
JA  "{n} 件の補正 | {n} 件の補正"

EN  "{device} is disconnected | {count} speakers are disconnected"
JA  "{device} は切断されています | {count} 台のスピーカーが切断されています"
```

## Chunks

`en.json` is split into `.i18n-parts/en/NN.json` (about 140 keys each). Translate
one chunk into `.i18n-parts/<lang>/NN.json` with the **same structure and keys**,
then merge and check:

```bash
python3 .i18n-parts/seed.py merge <lang>
python3 .i18n-parts/seed.py check <lang>
```

The merge step writes `ui/src/i18n/locales/<lang>.json` in the English key order.
Do not edit `en.json`, `ru.json`, or another language's directory.
