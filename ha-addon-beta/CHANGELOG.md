# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.1.0-beta.23] - 2026-10-30

### Fixed

- An Echo-style speaker now gets its audio sink on the first connect after pairing: when BlueZ answers "busy", the bridge asks again a few seconds later on a growing schedule instead of waiting for a signal that never comes. It also asks for the sink before switching the sound card profile on a reconnect, which used to fail with "Input/output error".
- A speaker that reconnects without an audio sink no longer starts a silent player on the default output; the device card says the sink is missing and what to do about it.
- A speaker that drops its Bluetooth link while the bridge is still waiting for its sound card no longer delays the reconnect: the search stops at once, so a freshly paired speaker becomes ready in about 15 seconds instead of nearly a minute.
- The group tuning clicks are 6 dB louder and can be heard on speakers that stay silent after a pause (the Lenco LS-500 never un-mutes for a short chirp): a new "Keep this speaker awake during tuning" switch plays a soft noise into that speaker only while a tuning session runs, so its amplifier is open when the clicks arrive; the other speakers keep clean clicks.
- Learned air latency no longer stops learning when the starts of a cheap speaker scatter by more than the 15 ms band (they were seen 20–40 ms apart): the reference then falls back to the median start, so the speaker reaches its third start and the air correction switches on.
- The air sensor works on Python 3.14: that version refuses to read Bluetooth monitor sockets with `recvmsg`, so the bridge now falls back to plain reads and says the air times come from the receive time instead of the kernel's microsecond stamp.
- Playback now stays on A2DP by default: when the host restores a saved headset (calls) profile, the bridge switches the speaker back and says so, instead of silently playing 8 kHz mono sound with the microphone off-limits. A new "Allow the headset profile (calls)" switch in a speaker's settings keeps the headset profile when you want calls through that speaker.

## [3.1.0-beta.21] - 2026-10-30

### Changed

- Manual tuning in group sync is now one screen with no steps: Start clicks (stops the group's music after asking, then plays the clicks) with the click type, a volume slider and a latency compensation control per speaker, and Save. Opening the tab starts nothing; changes are heard live, Save keeps them and ends the session, Discard changes puts the old values back, and leaving with unsaved changes asks whether to keep them. Solo and compare-two moved into a collapsed section, and the save-as-profile offer on leaving is gone (profiles are still saved from the Group tab).
- The speaker's sync error is now shown from the first second instead of a "Warming" chip; a "Settling" tone appears only while the error is out of tolerance or a jump happened, and warm-up ends as soon as the sync has held steady for about 3 seconds (60 seconds at most) instead of always after 60 seconds. A short underrun under one second no longer restarts the warm-up.

### Fixed

- The web UI reconnects by itself after Home Assistant ingress idles; no more stale "connection lost". It polls meanwhile, retries with backoff, reconnects when the tab or network returns, reloads once if the session expired, and offers a Reload button after 30 seconds.

## [3.1.0-beta.19] - 2026-10-30

### Added

- The web interface now starts in your Home Assistant language when you have not picked one, loads only the language you use, and its translations are ready for community contributions (see `docs/translations.md`).
- Playout sync can now be set to "Measure and correct" in Settings; it corrects each start's placement using the experimental air sensor and behaves like measure without it.
- Handing over or disabling a single speaker now runs at once and shows an Undo button in the confirmation toast.

### Changed

- Turning an adapter off or rebooting it, importing a configuration, restarting the bridge and updating the bridge now ask first and say what stops (the speakers on the adapter, playback, what is playing).
- Actions now follow one rule: standby, wake, enable, mute all and power save run at once with Undo, while removing a speaker, forgetting a Bluetooth pairing, resetting an adapter, importing a configuration (with a link to download the current one first), restarting and updating ask first and list what happens. Bluetooth scan, pairing, update and restart show what is running, for how long, Cancel where possible, and a next step when they fail; an update says "Updating… the bridge restarts".
- Error notifications stay until dismissed, the others pause while hovered or focused, and text fields use the same keyboard focus outline as buttons.
- Speaker actions and states now have consistent names in English and Russian: Release / Reclaim, Power save, Standby / Wake, Enable / Disable, Speaker latency compensation, and Air-to-sound latency.
- Messages from the bridge (guidance, recovery cards, the header status, action buttons, event timeline and blocked-control reasons) now follow the chosen language instead of always being English.
- Connection status now uses three separate names everywhere (header, cards, speaker details, Diagnostics): Audio link, Music Assistant account and Bluetooth connection, instead of one "Connected"; the Diagnostics verdict and the header status come from the same list of issues, so Healthy no longer appears above warnings.
- The speaker panel now has four tabs that fit a phone: Overview (state, the one main action, the audio path and the latest events), Settings, Timing (one value, a link to tune with the group, technical details folded) and History. Settings save as you change them with Saved and Undo (a refused change goes back and shows the reason), recovery tools sit under Troubleshoot (Pair again now asks first), and adapter power and reboot moved to Settings, Bluetooth.
- Calibration now has one path: a speaker's Timing tab no longer has its own click track or microphone comparison and instead offers "Tune with your group" (or explains how to make a sync group); opening the Manual tab no longer starts a tuning session, the wizard stops the music and starts the clicks in one confirmed step, and the measured Bluetooth latency is shown as a result ("Bluetooth latency measured: N ms, saved"). The matching API endpoints remain and are marked deprecated for the web UI.
- The web UI now has a "Show technical details" preference (preferences menu, and a default in Settings): off for new installs and on for installs that already had speakers, it hides signal levels, sync counters, Playout, server IDs, the audio path and advanced settings, and shows the Bluetooth and Audio link chips only when something is wrong. Inside Home Assistant the page drops its own brand header and Sponsor link, folds the runtime row into one status pill, starts with a collapsed sidebar, and replaces Security and update settings with "Signed in through Home Assistant" and "Updates come from the Home Assistant add-on store".
- The Web UI guide (English and Russian) is rewritten for the new interface: pages, the three connection states, the speaker panel, the action rules, Show technical details and the Home Assistant layout.

### Fixed

- An update that never finishes now says so after 3 minutes ("The update did not finish") and points to the logs, instead of showing "Updating…" forever.
- Signing in with a correct password now takes you to the home page
  instead of leaving you on the sign-in page with an error, and opening
  the sign-in page while no sign-in is required goes to the home page
  instead of showing a blank screen.
- Bug report: the minimum title and description lengths are shown up front, a diagnostics load error appears inside the dialog with Retry, and the submit button says what it will do (open a GitHub issue, send, or copy).
- Logs: a failed load says so with Retry instead of "No log entries", and applying the log level confirms or reports the result.
- Music Assistant sign-in prefills the server address from the speakers' Sendspin server and says when Auto-Discover finds nothing; the empty Groups page links to Music Assistant to create a group.
- "No results" on the speaker list offers Clear filters, and filters are cleared when the filter bar hides.
- Close buttons, the sidebar toggle and the device drawer's signal path are translated (RU) and the sidebar toggle announces its state; about 130 unused texts were removed.
- Accessibility and legibility in the web UI: keyboard focus is now visible on every control, warning text and the secondary and tertiary grey text meet WCAG AA contrast in both themes, dialog, drawer and toast close buttons have a 40 px touch target, and the selection volume slider and the phone update badge have accessible names.
- Recovery actions on Diagnostics are buttons you can press, the restart error banner can be dismissed and opens the logs, Cancel works while a speaker is recording or the phone sequence plays, "Require sign-in" cannot be switched on before a password exists, and volume, mute and hand-over failures name the speaker and the reason.
- Diagnostics: the Recovery toolbar wraps on phones instead of widening the page, and the tabs and copy buttons follow the interface language. The air sensor setting stays off until playout sync is in measure or loop mode, and its "seconds ago" value keeps counting.

## [3.1.0-beta.18] - 2026-10-29

### Added

- Diagnostics warn when the host suspends a Bluetooth speaker's audio sink
  between songs (PipeWire's suspend timeout, PulseAudio's suspend-on-idle);
  speakers can shift by tens of ms after such a pause. The bridge never
  changes the host setting.
- Experimental air sensor (`EXPERIMENTAL_AIR_SENSOR`, off by default):
  for SBC Bluetooth speakers in playout sync `measure` mode the bridge
  measures when the audio it wrote actually leaves over the air, shows
  this host placement in the device's timing panel, and the group sync
  wizard can calibrate each speaker's air-to-sound latency against an
  on-schedule reference (the panel lists it, with Forget). It does not
  change playback, needs the `CAP_NET_RAW` capability, and takes effect
  after a bridge restart.
- Playout sync `loop` mode now corrects Bluetooth speakers from the
  experimental air sensor (`EXPERIMENTAL_AIR_SENSOR`): each start is placed
  where the speaker's audio really leaves over the air, on the schedule when
  the speaker's latency is calibrated in the group sync wizard, otherwise
  where it played on average in its previous starts. The speaker's delay
  setting keeps working as a trim: changing it moves the speaker by exactly
  the change. The timing panel shows the correction, and Forget also clears
  the learned latency. Diagnostics tell when a `loop` speaker runs without
  its air correction.

### Fixed

- In playout sync a delay change now moves the sound by exactly the change;
  before, it also removed a small leftover offset (up to the dead band).
- After an underrun, playout sync could place the recovered audio up to
  100 ms late.
- A delay change during an inserted silence could freeze the playout
  controller.

## [3.1.0-beta.17] - 2026-10-28

### Added

- Device details show the delay a Bluetooth speaker reports for itself
  (A2DP Delay Reporting) when the speaker supports it, and the log notes
  when that reported delay appears or changes by 1 ms or more; speakers
  that do not report a delay show nothing extra.

### Changed

- **Recover a missing audio sink** (experimental): a speaker that is
  both an audio source and an audio sink, such as an Amazon Echo, is
  asked for its audio sink as soon as it connects, including when it
  reconnects on its own, before it can start sending audio to the
  bridge. When no audio output appears, the bridge asks again while the
  speaker's source stays connected, and only if that is refused drops
  the source and asks once more; each step and BlueZ's reason for a
  refusal is logged. Such a speaker that refuses and drops the link is
  no longer disconnected and reconnected, which locked an Echo out
  until it was paired again
  ([#476](https://github.com/trudenboy/sendspin-bt-bridge/issues/476)).
- Under the Home Assistant add-on, the Log level setting and the log
  viewer note that the add-on's own `log_level` option sets the level
  again on every restart
  ([#476](https://github.com/trudenboy/sendspin-bt-bridge/issues/476)).

### Fixed

- Refused Bluetooth profile requests now log BlueZ's reason instead of
  a bare "refused", and a failed A2DP sink request on connect is logged
  with its reason ([#476](https://github.com/trudenboy/sendspin-bt-bridge/issues/476)).

## [3.1.0-beta.16] - 2026-10-27

### Fixed

- `PLAYOUT_SYNC=measure`: steady corrections now need the timing error
  to persist for 5 s, so the sawtooth of the PipeWire output clock no
  longer triggers them; real drift is still corrected
  ([#556](https://github.com/trudenboy/sendspin-bt-bridge/issues/556)).
- `PLAYOUT_SYNC=measure`: one steady correction moves the audio by at
  most 50 ms; a larger persistent error is corrected in several steps
  instead of one large jump that could drop up to half a second of audio
  ([#556](https://github.com/trudenboy/sendspin-bt-bridge/issues/556)).
- `PLAYOUT_SYNC=measure`: after an underrun or a stalled output, the
  timing error is measured from a fresh, stable baseline before steady
  corrections resume, so an offset that appears while the output
  recovers is reported instead of corrected; one over 50 ms shows as a
  warning and a degraded state
  ([#556](https://github.com/trudenboy/sendspin-bt-bridge/issues/556)).

## [3.1.0-beta.15] - 2026-10-27

### Fixed

- Group sync tuning now measures speakers with ordinary microphones in
  noisy rooms (a headset or laptop microphone), where it reported "not
  heard" although the speakers were clearly audible. All clicks of a
  recording are added up, so speakers are measured even when each single
  click is lost in the room's noise; the short 6-second measurement at a
  speaker still needs its clicks a few dB above the room's noise
  ([#553](https://github.com/trudenboy/sendspin-bt-bridge/issues/553)).

## [3.1.0-beta.14] - 2026-10-27

### Fixed

- In `PLAYOUT_SYNC=measure` the output now opens at the speaker's own sample
  rate. It read a wrong rate (for example 32681 Hz) and resampled every
  stream needlessly.

## [3.1.0-beta.13] - 2026-10-27

### Added

- Playout telemetry for every player: PulseAudio stream latency and system
  clock slew in the timing panel.
- Experimental `PLAYOUT_SYNC=measure`: the bridge times Bluetooth playout
  itself and shows the playout error against the Sendspin clock. On a seek
  or skip the old audio still in the output buffer is muted.
- A per-speaker "Playout sync" switch to opt a single speaker out of the
  bridge's own playout timing while `PLAYOUT_SYNC` is on.

## [3.1.0-beta.12] - 2026-10-27

### Added

- With "Recover a missing audio sink" turned on, a speaker that is also an
  audio source, such as an Amazon Echo, is asked to stop sending audio to the
  bridge and to accept it instead, before the disconnect/reconnect. Echo
  devices could connect but never got an audio output.
- The log now lists the profiles a speaker offers when it connects, and the
  profiles its sound card has when the bridge switches it to audio output,
  so a report about a missing audio sink shows what the speaker can do.

## [3.1.0-beta.11] - 2026-10-26

### Changed

- The Sendspin library is updated to aiosendspin 10.2.1. Its fixes include
  reconnecting to a client at its new address after an IP change and no
  playback gaps when a client's network addresses change.

## [3.1.0-beta.10] - 2026-10-26

### Added

- Speakers now offer Music Assistant 24-bit audio at 44.1, 48 and 96 kHz
  besides 16-bit. A player set to a hi-res output format in Music Assistant
  gets it instead of falling back to 16-bit; players left at their default
  stay on 16-bit 48 kHz.

### Fixed

- Saving the settings no longer revokes the tokens issued for the Home
  Assistant integration. Each save dropped them from the configuration, so
  Home Assistant lost access to the bridge until it was paired again.
- Home Assistant no longer waits 60 seconds at startup for the Sendspin
  Bridge integration ("Setup timed out for bootstrap"). Its event listener
  now runs in the background.

### Security

- Updated aiohttp to 3.14.5, Pillow to 13.0.0, certifi to 2026.10.23 and
  cryptography to 50.0.2, which fix published vulnerabilities in those
  libraries.

## [3.1.0-beta.9] - 2026-10-17

### Fixed

- Changing a speaker's delay while it plays no longer restarts its audio
  stream. Each change used to open a new stream, and a Bluetooth speaker
  buffers every new stream by a different amount, so each change landed at
  a random offset of up to hundreds of ms and a speaker could not be tuned by
  ear. A change of up to 2 s now applies to the stream that is playing: the
  audio already sent to the speaker plays out, then the bridge inserts
  silence (later) or skips audio (earlier) to the exact sample, with a 5 ms
  fade so nothing clicks. The new delay is heard after the audio already
  buffered, typically a few hundred ms. Larger changes still restart the
  stream. The log says which was done, for example `Output delay changed
  283→112 ms; shifted in place (inserted 171 ms)`.

- A speaker's process no longer grows by a whole audio pipeline with every
  stream start or output rebuild: the pipelines it closed were never freed.
  When a delay change does restart the stream, the new stream is now opened
  in the background, like at a stream start, so the speaker's process
  (time sync, volume and stop commands included) is not held meanwhile.

## [3.1.0-beta.8] - 2026-10-17

### Fixed

- On a Bluetooth speaker the first audio of every stream reached the player
  0.7–1.6 s after its play time: opening the audio output, which waits for
  the speaker's Bluetooth link, held the speaker's process (time sync,
  volume and stop commands included) while Music Assistant's first audio
  waited unread. The output is now opened in the background and the audio
  that arrives meanwhile waits for it. With the default
  **Compensate the audio buffer latency**, audio whose play time has passed
  by the time the output is ready is dropped to the exact sample instead of
  being handed to the audio server, at a start or after a dropout. The audio
  server still clips the few tens of ms just ahead of what the speaker is
  playing (about 60 ms on Bluetooth). A speaker whose output is slow to open
  therefore joins a group stream in time, a moment after the others; the lead
  time declared to Music Assistant is unchanged. The FLAC decoder is loaded
  in the background a few seconds after the speaker starts, not on its first
  stream. The `Stream start timing:` line now also gives the overdue audio
  dropped, how late the first kept sample reached the output, how long the
  output took to open, and how long the start held the speaker's process.

## [3.1.0-beta.7] - 2026-10-16

### Added

- Timing diagnostics in the log, for setting beside a phone measurement:
  every stream start logs one `Stream start timing:` line (pipeline latency,
  PulseAudio stream latency, time sync offset and error, how early the first
  audio arrived, output delay); every Bluetooth connect logs the speaker's
  transport once (`BT transport …`: codec, the speaker's reported Delay or
  "absent", Volume); while playing, a `Clock diag:` line gives the system
  clock rate against the Sendspin clock (every 10 s at debug level, once a
  minute at info). Settings → Audio → **Playback clock diagnostics**
  (`PLAYOUT_DIAG`, advanced) adds the rate of the sink's own audio clock.

### Fixed

- Speakers played behind the schedule by the PulseAudio buffer's latency,
  an amount that depended on `PULSE_LATENCY_MSEC` and the audio server
  (on PulseAudio 17 about 110 ms at 200 ms, 310 ms at the default 600 ms and
  410 ms at 800 ms; PipeWire reports other values). The bridge now fixes that
  latency and takes it out of the schedule, so audio leaves the audio server
  at its scheduled time whatever `PULSE_LATENCY_MSEC` is. Groups do not start
  later: the lead time and buffer declared to Music Assistant stay as
  configured, and the lead is raised only if a sink is measured to need more.
  Delays tuned before this release absorbed the old offset and need tuning
  again (lower by the `sink min` value the new start line shows). Settings →
  Audio → **Compensate the audio buffer latency** (`COMPENSATE_OUTPUT_LATENCY`,
  advanced, on by default) turned off restores the old timing. In Docker the
  `SENDSPIN_COMPENSATE_OUTPUT_LATENCY` and `SENDSPIN_PLAYOUT_DIAG` environment
  variables override both settings.

## [3.1.0-beta.6] - 2026-10-16

### Added

- Group sync tuning: manual tuning, and the fine-tuning by ear after a
  measurement is applied, have a choice of sound to listen to —
  **Clicks** (one woodblock-like click a second, the new default), **Clicks
  with accent** (every 4th click louder and higher, to catch speakers hundreds
  of milliseconds off) and **Noise** (pink noise, for the last few
  milliseconds between two speakers heard from midway). Switching while it
  plays starts the new sound and keeps the muted and soloed speakers. While
  speakers are measured the measurement's own click track plays. A sound file
  that cannot be written (full disk) is reported as such. New `PUT
  /api/v1/sync/sessions/{id}/sound` endpoint and `sound` in the session.

### Fixed

- In the Home Assistant add-on, signing in to Music Assistant through Home
  Assistant finds a Music Assistant add-on installed in the local add-on
  folder (for example `local_music_assistant_dev_local`), not only the
  official one, when the configured Music Assistant address is that add-on's.
  An add-on from another repository is used only when named in the new
  `ma_addon_slug` option (`MA_ADDON_SLUG`); an add-on is never trusted for
  calling itself Music Assistant, and when several match, none is used. The
  error lists the add-ons found.
- In the Home Assistant add-on, the bridge signs in to Music Assistant by
  itself on any page of its web UI, and again as soon as Music Assistant stops
  accepting the stored token, instead of only on the Groups page. Repeated
  sign-ins back off, the bridge revokes its own older tokens in Music
  Assistant instead of piling them up, and `ma_auto_silent_auth` turned off
  now stops it.
- When Music Assistant has no Home Assistant OAuth sign-in configured, the
  error now points to the automatic add-on sign-in or to pasting a Music
  Assistant token instead of only to the password sign-in.

## [3.1.0-beta.5] - 2026-10-16

### Added

- Settings → Music Assistant can connect with a pasted token: choose "Use a
  token instead" and paste a long-lived token created in Music Assistant →
  Settings → Profile. The bridge checks the token with Music Assistant before
  saving it.
- Group sync tuning: align the Sendspin players of a Music Assistant sync
  group from the bridge — manual tuning by ear with a click track, solo and
  compare-two listening, or a measurement with a microphone after which the
  measured delays are applied and fine-tuned by ear on the same screen; named
  delay profiles (`SYNC_PROFILES`). The group is stopped for a session, the
  click track ("Sendspin sync clicks") replaces the group's queue, solo mutes
  the other members, and the volumes changed for the clicks go back at the
  end. Speakers that cannot be muted, are
  unavailable, silent or muted are flagged before anything is touched. New
  `/api/v1/sync/*` endpoints and `sync` events.
- Measure with a phone: a link and QR code open a capture page on the
  bridge; press Start on the phone and the bridge plays each speaker alone in
  turn while the phone records once. Over HTTPS (a separate port with a
  self-signed certificate, `SYNC_CAPTURE_HTTPS_PORT`) the phone records in the
  browser and stops by itself; over plain HTTP it uses the phone's recorder
  with a countdown. The link only lets the phone follow the measurement,
  start its sequence and send its recording, expires after 30 minutes and
  stops working when the measurement ends.

### Fixed

- Signing in to Music Assistant with Home Assistant works again with current
  Music Assistant, which secures that sign-in with PKCE. It used to fail after
  the password or two-factor code with "Failed to exchange HA code for MA
  token". Older Music Assistant keeps signing in as before.

- A delay changed for a player in Music Assistant is now kept by the bridge:
  the player shows it, saves it to its settings, and starts with it after a
  restart, so Music Assistant and the bridge agree. This works for audio
  outputs as well as Bluetooth speakers.

- Changing a speaker's delay, in Music Assistant or on the bridge, takes
  effect at once and exactly, even for a few milliseconds. Small changes used
  to do nothing and large ones arrived late, as a jump. The speaker may go
  silent for a moment when the delay changes.

## [3.1.0-beta.4] - 2026-10-15

### Added

- Shuffle and repeat buttons on a playing speaker, and seeking by clicking or
  using the arrow keys on its progress bar.
- Each speaker shows which Music Assistant it is connected to, and warns when
  that is not the bridge's own Music Assistant.
- A bridge accepts only its own Music Assistant (the one in its settings, or
  one on the same machine) when another Music Assistant on the network tries
  to take a speaker. Refusals are listed in the speaker's details. The
  `SENDSPIN_OWN_SERVER_ONLY` setting turns this off.

### Changed

- Disabled speakers appear as regular cards and rows, dimmed, in the same
  grid or list, filters and sorting, instead of a separate list.
- Enabling a speaker starts it at once, without a bridge restart.
- The grid/list switch is available from the first speaker.

### Fixed

- A list row keeps its volume slider and actions menu in view, and its status
  on one line.
- Removing a disabled audio output takes it off the dashboard at once.

## [3.1.0-beta.3] - 2026-10-14

### Added

- Speaker cards show what the previous interface showed: album art (click to
  enlarge), a sync badge with the drift and re-anchor count, the Music
  Assistant group (opens the group in Music Assistant), the Bluetooth adapter
  (opens its settings) or the audio output, signal-strength bars that grey out
  when the reading is old, how long a speaker has been in standby, and whether
  it was released automatically or handed over.
- More precise speaker states: buffering, muted output, stopping and
  reconnecting.
- A short "Why?" note on a card when an action is unavailable for a reason
  its status does not already show.
- In the list view a row opens in place to show playback, controls and
  details; the most active speaker opens on load.
- Sorting by activity (playing first, members of a Music Assistant group kept
  together), by name or by status, remembered in the browser, and a filter for
  Bluetooth speakers or audio outputs when both are present.
- Power save, standby and hand-over for the selected speakers. Bulk actions
  skip speakers they cannot apply to and name each speaker that failed.
- A runtime line under the header: install type, host name, IP address,
  uptime, connected speakers, Music Assistant and playback.
- The API reports when a speaker's signal strength was last measured
  (`bluetooth.rssi_at`).

### Fixed

- In the list view, local audio outputs show an output icon instead of a
  Bluetooth one.
- On narrow screens the guidance banner puts its button under the text
  instead of squeezing the text into a narrow column.

## [3.1.0-beta.2] - 2026-10-14

### Changed

- The Sendspin library is updated to aiosendspin 10.0.2, which carries the
  upstream bug fixes since 10.0.0, including pairing records that stay
  readable and match after an interrupted pairing.

## [3.1.0-beta.1] - 2026-10-14

### Added

- Local audio output players: besides Bluetooth speakers, the bridge can play
  to an audio output of the computer it runs on (a USB DAC, a sound card,
  HDMI) as its own Music Assistant player. Add one from the new "Audio output"
  tab of the Add speaker dialog. The output is found again by its hardware
  identity, so it keeps working after a replug or a rename. When it is
  unplugged the card shows "Output missing" and playback waits for it; when it
  comes back the player resumes by itself.
- The bridge's own volume control is used for outputs by default, so a shared
  sound card's volume is not changed unless the player is set to use the
  system volume. Diagnostics warn when an output is muted, at zero volume or
  unplugged, and the optional `PULSE_SINK_RESET_ON_START` setting wakes
  each output once when the bridge starts.

### Changed

- The configuration file moves to a new format on the first start: speakers
  are stored as players, and their saved volume, audio output and "released"
  state are kept apart from the settings. A copy of the previous file is saved
  as `config.v5.bak.json`. Going back to a 2.x version after this update starts
  without speakers. On Docker and LXC installs, restore `config.v5.bak.json` as
  `config.json` before going back to keep them. In the Home Assistant add-on,
  go back by restoring the add-on backup Home Assistant offers to take before
  the update.
- The bridge's HTTP API (v1) now reads and writes speakers as `players` (each
  with a stable id and a backend) instead of `BLUETOOTH_DEVICES`; scripts that
  edit the configuration through the API need the new shape. Configuration
  files in the old format are still accepted by import.

### Removed

- The Home Assistant add-on no longer lists speakers in its Configuration tab.
  Speakers are added, changed and removed in the bridge's web interface; the
  speakers already set up are kept when updating, and the add-on's old list is
  ignored from then on.

### Fixed

- A speaker's power-save delay outside 0-60 minutes (for example after
  updating from a long legacy delay) is corrected to the nearest limit instead
  of blocking every settings save.
- Saving the settings without changing anything no longer restarts the running
  speakers.
- Duplicate-device warnings name the speaker instead of showing its Bluetooth
  address.
- In the Home Assistant add-on, the bridge kept asking to connect Music
  Assistant ("Music Assistant API URL is not configured") although Music
  Assistant was found automatically and connected. The check now follows
  the live connection, and "Revalidate MA link" uses the address found
  automatically.

## [3.0.0-beta.21] - 2026-10-11

### Changed

- Disabling a speaker moves it to the disabled speakers at once instead of
  leaving it among the speakers as "Disabled / Handed over" until the
  bridge restarts; enabling it there brings it back without a restart.

### Fixed

- Removing a speaker that is running answered "Unknown device" and left it
  in place; removing it from its menu or its settings now works.
- A removed speaker no longer stays on the page as "Handed over" until the
  bridge restarts, including one that was disabled since the last start
  (by hand, or by the bridge after failed reconnects).
- The confirmation for removing a speaker from its settings opened behind
  the speaker's panel and could not be clicked.

## [3.0.0-beta.20] - 2026-10-10

### Fixed

- A speaker connected to a Music Assistant server set by address
  (`SENDSPIN_SERVER`) showed "Sendspin is not connected" and, after half a
  minute, "Cannot connect to Sendspin server" while it played. Such
  connections are now shown as connected.
- Music Assistant no longer logs "Invalid command: subscribe_events" each
  time the bridge connects; the bridge stopped asking for a subscription MA
  does not have (it sends its events to every signed-in client).

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
