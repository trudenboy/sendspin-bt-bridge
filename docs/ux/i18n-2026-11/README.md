# Interface languages — visual check (2026-11)

Evidence for the first eight-language release (`3.2.0-beta.10`).

- **Languages:** de, es, fr, it, ja, ko, tr, zh-Hans.
- **Routes:** Home, Groups, Settings, Diagnostics, Hardware, Sync.
- **Viewports:** 375 px and 1280 px, light theme; German and Japanese also in
  dark theme on Settings and Diagnostics.
- **Method:** `DEMO_MODE=true`, Playwright (Chrome), the locale set through
  `sendspin-ui:locale` before the app boots.
- **Automated check:** `overflow.json` records
  `document.documentElement.scrollWidth` against `window.innerWidth` for every
  route/language at 375 px.

The first pass found two overflows (Italian and Japanese on Groups) caused by
the Music Assistant account line not wrapping a long URL, and cramped Japanese
labels in the first-run checklist. Both were fixed in
`ui/src/views/GroupsView.vue` and
`ui/src/components/hardware/FirstRunChecklist.vue`; the final run is
**48 checks, 0 problems**.

Screenshots are JPEG at quality 80 to keep the repository small; they are
evidence, not pixel-perfect baselines.
