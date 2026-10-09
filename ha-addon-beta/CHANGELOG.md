# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
