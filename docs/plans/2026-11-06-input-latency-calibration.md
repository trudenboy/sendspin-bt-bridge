# Input latency calibration (parked design)

Designed 2026-11-06 by grilling, deliberately not built in the same pass as
ADR-0005. Kept so the decisions survive.

## Why it exists

A live input fed into Music Assistant carries its own latency: Bluetooth
decode, driver buffering, capture chunking. Group sync does not need it (a
constant cancels out of a relative measurement), but using a live source
alongside other sources — a TV, a musician's own instrument, another room's
direct feed — does.

## Decisions taken

- **The number belongs to the input**, stored beside gain and mute in
  `SENDSPIN_SOURCE_INPUT_STATE`, with `delay_ms`, `measured_at`,
  `source` (`measured` | `estimated`) and a device/profile `fingerprint`.
- **It is applied in one place**: the capture timestamp handed to
  `SourceClient.feed()`. Music Assistant owns player delays and re-asserts them
  (`set_output_delay` → `static_delay_ms`), so baking an input offset into
  players would be both wrong for other sources and overwritten.
- **Measurement**: loopback where the bridge owns the signal start (local
  inputs, a cable loop through an output) — play the click track, catch it at
  the capture, take arrival minus start. Where it does not (a phone over A2DP,
  a headset microphone) the click's periodicity leaves the absolute offset
  unrecoverable, so the value is a model estimate from the pipeline (10 ms
  chunks, profile, buffers) plus manual correction, badged as an estimate and
  never passed off as a measurement.
- **Reference**: the group's players, measured through the existing path.
- **Verdict thresholds**, matching the group's own vocabulary: ±5 ms excellent,
  ±15 ms fine for music, ±30 ms audible on percussive material, worse →
  re-measure.
- **Staleness**: no expiry by time; a device or profile change (A2DP ↔ HFP,
  a different node, a different headset) marks the value as needing a re-check.
- **UI**: a step of the existing measurement wizard, entered from the input's
  card; no second application.
- **Failure**: fall back to the estimate with an explicit badge, never silently
  present an estimate as a measurement.

## Open when picked up

- Whether the estimate's model coefficients are good enough to ship without a
  first loopback measurement on the same host.
- Whether a measured value should also be pushed to Music Assistant if it ever
  accepts a source-side offset.
