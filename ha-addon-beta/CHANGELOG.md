# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
