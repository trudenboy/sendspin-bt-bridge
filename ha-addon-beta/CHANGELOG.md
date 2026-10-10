# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.0.0-beta.19] - 2026-10-10

### Fixed

- A removed speaker no longer stays listed as disabled until the bridge
  restarts.
- Signing in to Music Assistant with the Home Assistant account works again
  in the add-on. Newer Music Assistant versions refuse the add-on's direct
  sign-in request; the bridge now signs in through Home Assistant instead.

## [3.0.0-beta.18] - 2026-10-10

### Added

- Speakers can be removed from the bridge again — from a speaker's menu,
  at the end of its settings, or from the list of disabled speakers. Its
  player leaves Music Assistant and its Bluetooth pairing is removed.
- Disabled speakers are listed under the speakers, where they can be
  enabled again or removed.

### Changed

- A speaker's actions read the same everywhere and are grouped by what
  they affect: its connection (reconnect, standby, active source), its
  place on the bridge (hand over to other devices or take back, disable)
  and removal. "Forget Bluetooth bond", which left the speaker on the
  bridge trying to reconnect, is replaced by removal; "Pair again" in the
  speaker's details still repairs a broken pairing.
- A speaker handed over to other devices shows as "Handed over", and the
  power-saving action is called "Suspend audio output" so it is not mixed
  up with handing the speaker over.

### Fixed

- Settings did not open on installs whose speakers have keep-alive off
  (an interval of 0): loading them failed and the page kept spinning. The
  settings now load such configurations, and if loading ever fails the
  page says so and offers to try again.
- The container's health check asked for an address the new API no longer
  had, so Docker reported the bridge as unhealthy.
- Volume changed on the bridge — a speaker's or a group's slider, or the
  speaker's own buttons — now reaches Music Assistant. MA kept showing the
  old level and could restore it later.
- Speakers of one Music Assistant sync group show as one group on the
  Groups page, and group volume and pause act on all of them. Each speaker
  used to appear in a group of its own with no id.
- Each group on the Groups page shows its own track. One group's track
  (often a stale one from an idle group) used to appear on every card; an
  idle group's last track is now marked as not playing.
- Play/pause on a group card goes through the group's own Music Assistant
  queue, so it works for groups whose speakers are on another bridge.
- Group members on the Groups page show their state again: green while
  playing, amber when Music Assistant cannot reach them. The page follows
  changes made in Music Assistant without a reload.

## [3.0.0-beta.17] - 2026-10-10

### Added

- "Add speaker" is a guided flow: the search starts by itself, a found
  speaker pairs with one click, then you give it a name and a room and play
  a test sound. Compatibility options and already-paired speakers are one
  step away for when the speaker is not found.
- Settings can be searched, and edited-but-unsaved options are marked.
  Section headings stand apart from the options under them.
- A notice appears when the page loses its live connection to the bridge,
  so stale information is not mistaken for current.
- Keyboard shortcuts: 1–4 switch places, A adds a speaker, / searches, ?
  lists them.

### Changed

- Four places instead of five: Speakers (the former dashboard and device
  list in one), Groups (Music Assistant groups, each with its now playing
  and controls), Settings and Diagnostics. Old addresses still work.
- The header shows the product name and logo (the same as the stable
  release, also as the browser icon), this bridge's name, its status and
  a Sponsor link with a heart; help
  (documentation, GitHub, bug report, version) and preferences (language,
  theme) are in two menus.
- Diagnostics has three tabs — Overview (health, what needs attention,
  recent events), Logs and System — and a "File bug report" button.
- Search and filters on the speakers page appear once there are enough
  speakers to need them.
- Speaker cards lead with what matters: the name without the bridge
  suffix, room, battery and signal in words, what is playing with its
  controls, and the volume. Status reads Playing, Connected, Standby, Not
  connected or Problem. The whole card opens the speaker's details; the
  MAC address and adapter moved there.
- Fewer pop-up confirmations for actions whose result is already visible.

- Buttons, links and status text keep readable contrast (WCAG AA) in the
  light theme; the bright blue stays for icons and sliders.
- Confirmations (forgetting a speaker, revoking a token, leaving unsaved
  settings, applying a measured delay) appear in the page's own dialog
  instead of the browser's.
- Phone layout: shorter labels in the bottom bar, one "+" to add a speaker,
  larger touch targets for menus and mute.
- Screen readers announce each speaker's volume slider by name and read
  the page in the chosen language; the browser tab shows the page and the
  bridge's name.

### Fixed

- Dividers in dialogs were drawn black.
- "Up to date" and "Update started" messages showed internal text keys.
- On a phone with Russian selected the devices page scrolled sideways.
- "Connected" and "Healthy" showed in blue instead of green, and health
  checks said "ok" untranslated.

## [3.0.0-beta.16] - 2026-10-10

### Added

- Settings cover every option of the bridge again, in sections named
  after what they do: Music Assistant (connection test, player control),
  Audio, Bluetooth (adapter names and device class, reconnect limits,
  recovery switches), Home Assistant (MQTT broker detection and test,
  integration status, rooms by adapter), Security (password, sign-in
  lockout, trusted proxies, API tokens), Updates, Guidance and Backup.
  Rarely needed options appear under "Show advanced settings". The audio
  buffer offers the latency assistant's presets with its recommendation.
- The web interface follows Home Assistant's dark mode and accent colour
  when opened from its sidebar.
- Each speaker's settings are back in its details panel: name, adapter,
  room, idle behaviour (power save, disconnect, keep awake and its signal),
  volume control and player port.
- A Timing tab per speaker: the delay applies at once, the bridge's
  suggested delay can be applied in one click, a click track helps compare
  speakers by ear, a microphone measurement compares two speakers and sets
  the later one's delay, and recent buffer and sync-error history is drawn.
- The dashboard shows what needs attention, as the bridge's guidance
  sees it, with the suggested fix one click away (open the right settings
  section, reconnect or re-enable speakers, unmute an audio output, run a
  check again, scan for speakers).
- A System tab in Diagnostics: uptime, memory, platform, audio server
  and BlueZ versions, each player process with its memory and restarts,
  all downloads in one place (diagnostics report, service log, recovery
  timeline, configuration) and webhooks for device and bridge events.
- Recovery actions per speaker — reconnect, pair again, let Music
  Assistant pair, unmute the audio output, release or resume audio — in
  the details panel's menu.

### Changed

- On a touch screen a tap on a volume bar changes the volume by one step
  toward the tap instead of jumping to the tapped level; dragging works as
  before. A stray touch can no longer turn a speaker up to full volume.
- Links that come from the bridge or Music Assistant (update notes, add-on
  install pages, Music Assistant's player page, filed bug reports) open
  only when they are ordinary web addresses.
- The web interface uses Home Assistant's colours, type and card shapes
  and Music Assistant's buttons, so it looks at home next to both.

### Fixed

- Changing the web interface password now uses the current password and
  takes effect at once; the settings screen used to put the new password
  into the configuration, which the bridge ignored.

- The settings no longer fail to load when a Bluetooth adapter is linked
  to a Home Assistant area: the API described that link as plain text and
  rejected the bridge's own answer.

## [3.0.0-beta.15] - 2026-10-09

### Added

- A new web interface, rebuilt as a single-page app on top of a public,
  versioned API (API v1 at `/api/v1`, documented at `/api/v1/docs`). The
  same API serves the web interface, the Home Assistant integration and
  your own scripts: bearer tokens from the security settings work for
  every endpoint.
- Live updates over one event stream: device status, the progress of long
  operations (scans, pairing, reconnects, update checks, Music Assistant
  discovery) and the service log arrive as they happen, over Server-Sent
  Events or a WebSocket.
- Long operations run as jobs you can follow or cancel, instead of
  each endpoint having its own way of reporting progress.
- Adding a speaker is one flow: pick the Bluetooth adapter, scan, then
  "Pair & add" puts it on the bridge. The compatibility options (pairing
  without a PIN prompt, allowing the hands-free profile, briefly
  disconnecting the other speakers on the adapter) apply to that attempt
  only.
- Sign-in offers every method the bridge supports — the bridge password,
  a Music Assistant account or a Home Assistant account, including Home
  Assistant's two-factor step.

### Changed

- The beta channel now carries everything released on the stable and
  release-candidate channels up to 2.77.0-rc.2, including Sendspin 1.0
  through aiosendspin 10 (Music Assistant 2.11 or later is required).

- Errors from the API are Problem Details documents with a stable `code`
  that clients can act on, and malformed requests are answered with the
  fields at fault.
- Speakers are addressed by their stable player id everywhere, never by
  name or by their position in a list, so commands keep reaching the
  right speaker after one is renamed, added or removed.
- Changing the local password asks for the current one.

### Removed

- The previous web interface and its private endpoints. The Home
  Assistant integration keeps working unchanged: the endpoints it uses are
  still served at their old addresses.

### Fixed

- Bug reports sent from the web interface without a GitHub account keep
  their log lines. The report is shortened to fit GitHub's size limit and
  the logs came last, so they were cut off entirely; they now come before
  the raw diagnostics data.
- The sign-in lockout can no longer be dodged behind a reverse proxy that
  does not name the client: each failed password attempt with a different
  user name used to start a fresh count.
- Importing a configuration file can no longer switch authentication on
  without a password, which would have locked everyone out.

### Security

- A Home Assistant ingress visit no longer leaves a signed-in session
  cookie behind. Browsers send cookies to every port of a host, so the
  cookie also opened the bridge's own port on the same machine.
- Cross-site pages cannot drive the API of a bridge without
  authentication, and the event WebSocket refuses other origins.
