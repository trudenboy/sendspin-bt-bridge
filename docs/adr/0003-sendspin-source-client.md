# ADR-0003 — The phone source is a Sendspin source client of Music Assistant

* **Status:** accepted (2026-11-01)
* **Date:** 2026-11-01
* **Plan:** [3.2.0-beta.2 — phone as a Sendspin source](2026-11-01-source-client-plan.md)
* **Research:** [Phone as an audio source on HAOS](../research/2026-11-01-phone-as-source-on-haos.md),
  [What to reuse from bluealsa2sendspin](../research/2026-11-01-bluealsa2sendspin-review.md)

## Context

Since 3.2.0-beta.1 the bridge can capture a phone's A2DP audio and offer it as a
Sendspin stream. It does so as a **server**: `BtSendspinSource` starts
`aiosendspin.server.SendspinServer` on its own port (default 8940), players
connect to the bridge, and a per-speaker `relay` plays the capture straight into
a `bluez_sink` for the speakers the bridge drives itself.

That shape has three problems:

1. **It inverts the topology.** Music Assistant is the Sendspin server for every
   player, including the bridge's own speakers. The source made the bridge a
   second server for one stream, so MA could only receive it by acting as a
   player of the bridge.
2. **It duplicates routing.** Playing the phone on a bridge speaker bypassed MA
   through the relay, with its own armed-device state, its own `/players` API and
   its own failure mode (`source_sink_missing`).
3. **It does not work on HAOS.** The server approach needs a capture source; the
   implementation is PipeWire-specific (`wpctl`, `pw-record`, `pw-play`) and
   `probe_capture()` is literally `which wpctl`. HAOS runs PulseAudio 17 in
   `hassio-audio` with `module-bluez5-discover`, so a phone that connects is
   PulseAudio's `bluez_source` and our code refuses before trying.

Research (`docs/research/2026-11-01-*`) established that PulseAudio can record
the phone's capture source with `parecord`, that Music Assistant 2.10+ ships a
`sendspin_source` provider exposing any `source`-role client, and that
`bluealsa2sendspin` already implements the source-client half against
`aiosendspin`'s public client API.

## Decision

The bridge is a **Sendspin source client** of the Music Assistant server:

* `Roles.SOURCE` with `line_sense`, static pairing code, one identity and
  pairing store per bridge, reconnect with backoff.
* The phone's PCM is captured locally (PipeWire `bluez_input.*` or PulseAudio
  `bluez_source.*`) and fed to `SourceCapture.feed()`; MA owns the clock, the
  jitter buffer and the routing.
* `server/command` `start`/`stop` from MA governs the capture; `line_sense`
  reports whether the phone is streaming.
* The bridge's own speakers stay ordinary MA players; there is no relay and no
  source server on the bridge.

Pairs with this decision:

* The source is configured by one 8-digit code shown in the UI and entered in
  MA's own "add source" flow; the code and the PSK survive a bridge restart.
* Capture is backend-agnostic: PipeWire on Docker/LXC, PulseAudio on HAOS.
* `SENDSPIN_SOURCE_PORT`, the server identity files, `/bridge/source/players`,
  `relay`, and the player's `source_stream_url` are removed.

## Alternatives considered

* **Keep the server inversion, add a PulseAudio capture backend.** Fixes HAOS
  capture but keeps two sources of truth for routing and a second Sendspin
  server that MA does not own. Rejected once `sendspin_source` was available.
* **Keep the relay as a local fallback.** Works without MA, but it is a second
  audio path to the same speaker that MA also drives, and it requires the player
  to be re-pointed at the bridge. Rejected for a beta product that is built
  around MA.
* **BlueALSA instead of host PulseAudio/ PipeWire.** BlueALSA is the right
  capture stack for a dedicated receiver host, but on HAOS the A2DP endpoints
  are already registered by `hassio-audio`; taking them over removes every
  speaker sink. Rejected for add-on mode; documented as a future standalone
  option.

## Consequences

* **MA 2.10+ with `sendspin_source` is required** for the feature. Older MA shows
  a stable `ma_source_unsupported` reason instead of a silent failure.
* **Two pairings exist and must be labelled as such**: the phone pairs with the
  bridge over Bluetooth; the bridge pairs with MA with an 8-digit code. The UI
  keeps them in separate blocks.
* **`aiosendspin==10.2.1` has no public `send_source_signal`.** The client reaches
  into `SendspinClient._admitted_connection` through a narrow adapter with a
  runtime shape check; the version is pinned exactly and an upstream issue asks
  for a public wrapper. An upgrade must update the adapter first.
* **The feature is unavailable without an audio server** that can record the
  phone (PulseAudio with `pactl`/`parecord`, PipeWire with `wpctl`).
* **Routing on the speaker is an MA action.** The bridge no longer offers
  "play the phone on this speaker"; the docs explain MA's source playback.
* **A bridge restart keeps the pairing** (identity and PSK on disk), so the code
  is not needed again unless the pairing is forgotten.
