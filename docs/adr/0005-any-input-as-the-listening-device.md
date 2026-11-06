# ADR-0005 — Any input can be the listening device of a group measurement

* **Status:** accepted (2026-11-06)
* **Date:** 2026-11-06
* **Plan:** [Any input as the sync wizard's listening device](../plans/2026-11-06-sync-input-listening.md)

## Context

The group-sync wizard measured a group by playing a click sequence through
Music Assistant and recording the room. The recording came from exactly one
place: a browser page served over HTTPS by the bridge and opened on a phone,
which uploaded int16 PCM back. That made the phone a required piece of
equipment, put the measurement behind a certificate prompt, and meant a bridge
that already had a perfectly good microphone attached could not use it.

The bridge already knows every input it can capture: local line-in nodes,
headset microphones, paired phones, and devices currently in source mode. They
are the same inputs the Source feed offers, and they are already openable
through the capture seam.

## Decision

### 1. The measurement takes a listening device

`GET /sync/listening-devices` lists every input the bridge knows, with the same
identity the Sources UI uses. `POST /sync/sessions/{sid}/capture/input` records
that input for the length of the session's own sequence and feeds the result
through the same decode and estimation path as the phone upload, so a member is
measured identically whichever device heard it.

The phone page remains as a fallback for a bridge with no usable input.

### 2. The input is borrowed, never selected

The recorder opens the node itself (`pw-record --target` / `parecord
--device`). It does not touch `SENDSPIN_SOURCE_ACTIVE`, the source client, the
headset profile or any sink: measuring a group must not change what the
operator is listening to. An input already captured by the Source feed is
refused (`input_busy`) instead of fighting it for the node.

### 3. Input latency does not have to be calibrated first

A constant delay in the listening device shifts every member's measured phase
by the same amount, and the proposal is the difference between members, so it
cancels. The wizard therefore accepts any input regardless of calibration and
says so in the report. Calibrating an input's own latency is a separate
problem — it matters when a live source must line up with other sources, not
when speakers must line up with each other.

### 4. The recording lasts as long as the sequence

The phone upload's own bound applies: `seq.max_duration_s(members, point)`. A
single-recording limit is the wrong ceiling — a two-member sequence already
runs 22 s, and truncating it silently drops the last speaker's solo.

## Alternatives considered

* **Keep the phone page only.** Requires a second device and a certificate
  prompt for something the bridge can already do itself.
* **Make the input the active Source input during measurement.** Simpler
  plumbing, but it stops whatever the operator was playing and leaves a
  different input selected afterwards.
* **Require input calibration before the wizard accepts it.** Would gate a
  measurement that is mathematically immune to the constant, and delay a
  working feature behind a separate design.
* **Measure the group end to end only.** Loses the ability to tell an input
  problem from a speaker problem.

## Consequences

* Additive public surface: `GET /sync/listening-devices` and
  `POST /sync/sessions/{sid}/capture/input`, with stable codes `unknown_input`,
  `input_unavailable`, `input_busy`, `capture_running`, `session_state` and the
  shared `recording_invalid`.
* The wizard gains a third capture mode beside "this device's microphone" and
  "phone", and reports the uncalibrated input as information, never as a
  limitation.
* A device in source mode can be measured with, but not borrowed while the
  Source feed holds it: the operator returns it to playback first.
* Input latency calibration — loopback measurement, model estimate for
  Bluetooth inputs, and a stored `delay_ms` per input — is designed separately.
