# Weblate setup checklist

One-time setup for the community translation project at
<https://hosted.weblate.org>. The repository files already exist; Weblate only
needs to be pointed at them. Keep `ru` out of the component: it is maintained
in the repository as the reference translation.

## 1. Account and plan

1. Sign in to <https://hosted.weblate.org> (or create an account).
2. Request the **Libre plan** at <https://hosted.weblate.org/hosting/>. It is
   free for public libre projects, with the limits of the 160k-string plan.
   - Repository: `https://github.com/trudenboy/sendspin-bt-bridge`
   - Licence: MIT
   - The project is public.
3. Install the **Hosted Weblate GitHub App**
   (<https://github.com/apps/hosted-weblate>) on the repository and grant it
   access to `trudenboy/sendspin-bt-bridge`. It handles cloning, pushing the
   translation branch, pull requests and incoming webhooks; a separate webhook
   is not needed.

## 2. Project and component

Create a project named `Sendspin BT Bridge` (slug `sendspin-bt-bridge`) and one
component named `Web UI` (slug `web-ui`):

| Setting | Value |
|---|---|
| Source code repository | `https://github.com/trudenboy/sendspin-bt-bridge` |
| Branch | `beta` |
| File mask | `ui/src/i18n/locales/*.json` |
| Monolingual base language file | `ui/src/i18n/locales/en.json` |
| File format | **JSON nested structure file** |
| Language code style | **BCP style using hyphen as a separator** (`zh-Hans`, not `zh_Hans`) |
| Template for new translations | **empty** — copying English would count as translated |
| Language filter | `^(?!ru$).+$` |
| Version control system | GitHub (via the Hosted Weblate app) |
| Push branch | `weblate-web-ui` |
| Push on commit | on |
| Translation licence | MIT |

After the first pull, the component should show **nine** target languages:
`de`, `es`, `fr`, `it`, `ja`, `ko`, `tr`, `zh-Hans` and `ru` if the filter did
not exclude it. If `ru` is present, open the Russian translation, select all
strings and use `Bulk edit` → `Add flags` → `read-only` under Translation
flags, so nobody edits the reference file by accident.

`zh-Hans` must keep its hyphen: the app resolves `zh-CN`/`zh-Hans` to that file
and rejects `zh-TW`/`zh-Hant`. Weblate reads either spelling, but the BCP style
keeps new files consistent.

## 3. Add-ons

Install on the component (Operations → Add-ons):

- **Cleanup translation files** (`weblate.cleanup.generic`) — removes keys that
  English no longer has.
- **Remove blank strings** (`weblate.cleanup.blank`) — a JSON file with `""`
  values would hide the English fallback in the UI.

Automatic translation is optional and needs a configured machine-translation
service. Without one, new English keys stay untranslated and fall back to
English until a translator fills them.

## 4. Translation instructions

Paste this into the component's **Translation instructions**:

```text
The web UI is a Vue app. English (en.json) is the source; do not change keys.

- Keep every {placeholder} exactly as written. Never translate text inside braces.
- Strings with " | " have two parts: the first is for one speaker, the second for
  several. Keep both parts and their placeholders. The second part often uses
  {devices} or {count}.
  - ja / ko / zh-Hans: real count plurals repeat the same wording in both parts;
    aggregate diagnostics differ and keep {devices}/{count} in the second part.
  - tr: after a numeral the noun stays singular ("3 hoparlör"), so real count
    plurals repeat the singular noun; aggregate diagnostics differ.
  - fr: the first part is used for 0 and 1, so it must be singular.
- Keep product and protocol names in Latin script: Music Assistant, Sendspin,
  Bluetooth, Home Assistant, Docker, PulseAudio, PipeWire, A2DP, SBC, AAC, LDAC,
  Wi-Fi, URL, IP, API.
- Keep configuration keys and code identifiers: SENDSPIN_PORT, POWER_SAVE_DELAY,
  hci0, bluez_sink.*, paths, log levels, and the literal <token>.
- Tone: de "du", es "tú", fr "vous", it "tu", tr neutral, ja です・ます,
  ko -습니다, zh-Hans neutral.
- The first seed was machine-assisted. Correct wording, grammar and terminology
  freely; the goal is a native-quality translation, not a literal one.
- Terms: docs/i18n/glossary.csv in the repository.
```

Add the glossary terms from `docs/i18n/glossary.csv` to the project glossary
(one language at a time) so future strings stay consistent.

## 5. Mark the seed for review

After the component imports the files, each language is 100% translated. Mark
the machine-assisted pass as **Needs editing** so reviewers see the queue:

1. Open the language, search `state:translated` (or leave the search empty).
2. Operations → **Bulk edit**.
3. Operation **Change string state** → **Needs editing**.
4. Preview, then **Apply all matching strings**.

The limit is 20,000 strings per operation; each language has 1,990. Repeat for
all eight languages. Strings stay translated and usable; the state is a review
flag.

## 6. Verify the first pull request

1. Edit one string in Weblate (or ask a translator to do it).
2. Weblate commits to `weblate-web-ui` and opens/updates a pull request into
   `beta`. If it only pushes the branch, open the pull request manually.
3. The PR must change only `ui/src/i18n/locales/*.json`. CI runs
   `npm run check:locales`, the build and the tests (PRs into `beta` are
   covered by `.github/workflows/ci.yml`).
4. Confirm there is no `zh_Hans.json` duplicate, no empty strings and no lost
   placeholders. If Weblate reorders keys, accept one normalisation commit;
   values must stay untouched.
5. Merge when green, then release the link to Weblate (`beta.11`): the
   `TRANSLATE_URL` constant and the `header.helpTranslate` key.

## Fallbacks

- **PR mode unavailable**: Weblate pushes `weblate-web-ui`; open the PR by hand.
  CI still runs because the PR targets `beta`.
- **Libre plan delayed**: translations are already in the repository and ship;
  the Weblate link stays off until the project is live.
- **`ru` not excluded by the language filter**: use the `read-only` translation
  flag described above.
- **Blank strings or reordered keys**: the add-ons and the normalisation commit
  above handle them; do not hand-edit Weblate output in `en.json`.
