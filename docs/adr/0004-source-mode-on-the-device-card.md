# ADR-0004 — A device's input and its output are one mode, switched on its card

* **Status:** accepted (2026-11-06)
* **Date:** 2026-11-06
* **Plan:** [Source mode on the device card](../plans/2026-11-06-source-mode-plan.md)

## Context

The Source feed arrived as a feature, not as a device state. Its only door was a
master switch in Settings; using a headset's microphone meant enabling the feed,
picking `headset-mic:<MAC>` from a list of inputs on the Devices page, and
ending it by disabling the whole feed. The Sources list mixed three unlike
things — paired phones, saved line-in endpoints, and the microphones of
speakers the operator had configured as players — behind one "Use" button, and
the speaker whose microphone was in use stayed in the players grid, stopped and
mute, with its volume and transport still drawn.

Operators read this as two separate systems. The one fact that explains it —
that a link is either carrying output or carrying input, never both — had no
representation anywhere.

## Decision

### 1. Source mode is a device state, and the card owns the switch

A Device whose link reports input capability wears one switch on its own card:
**Source mode** on, its card moves from the players list into the Sources
section, its Player stops, and the card shows the input's gain, mute and level;
**Source mode** off, it moves back and the Player resumes if it was running when
the capture took the link.

The switch is the same control in both places. The card is not duplicated
between sections: the Sources section is the answer to "what is an input right
now", so a device whose input was stolen by another returns to the players list
immediately.

### 2. Input and output are mutually exclusive per link

`headset-head-unit` and `a2dp-sink` cannot be active together, so a device in
source mode has no output to control. Its card therefore replaces volume and
transport with input controls instead of disabling them, and bulk output
actions skip it while still showing it, marked, where it moved to.

### 3. The Sources section lists inputs, not device kinds

It holds the paired phones, the saved line-in and microphone endpoints, and the
players currently in source mode. A player's microphone is no longer a separate
entry: it is the player's own card in its other mode. Adding a source means
pairing a phone or saving an endpoint — a microphone is never "added", it
arrives with the device that has one.

### 4. Level travels on its own event type

The bridge already computes the frames it forwards, so the level is a peak over
those frames. It is published as `source.level` at ~10 Hz, outside
`DEFAULT_TYPES` and outside the `status` payload: that payload is rebuilt whole
(~60 ms) on every change, and ten of those a second is a load regression on the
small hosts this bridge runs on. The SPA probes the type the way it probes
`hardware` (ADR-0002 §3).

### 5. Gain belongs to the input and is remembered

Gain and mute are properties of the input, not of the feed: muting silences the
capture without breaking the stream to Music Assistant, and the level the
operator set survives a restart, keyed by input id, in
`SENDSPIN_SOURCE_INPUT_STATE`.

### 6. The feature switch stays, and it is a prerequisite

`SENDSPIN_SOURCE` remains the "this bridge offers a Source feed" flag. While it
is off, no card draws a switch. Turning it off with an input active is a
confirmed action that releases the input first: a headset must not be left in
HFP with no capture behind it.

## Alternatives considered

* **Keep the global switch as the only way in and out.** Simplest, but it makes
  leaving source mode an act on the whole feature, which is what operators
  found wrong.
* **A per-player `source_mode` config key.** A second source of truth beside the
  active input id, which already persists and already names the device.
* **Show the device in both sections while in source mode.** Two cards for one
  device make bulk actions and counts ambiguous.
* **Poll a `/level` endpoint.** A second request path for a cosmetic value, and
  one that lags whatever cadence the page chooses.
* **Put the level in the `status` payload.** Correct shape, wrong cost: 10 Hz of
  full-status rebuilds.
* **Return to playback automatically when the link drops.** The operator chose
  the mode; a dropped link is an accident, and silently turning a microphone
  back into a speaker hides both.

## Consequences

* New public surface, all additive: `DELETE /bridge/source/active`,
  `PUT /bridge/source/active/volume`, `PUT /bridge/source/active/mute`,
  `volume`/`muted` on input rows, and the `source.level` SSE type with its
  stable codes (`no_active_input`, `input_unavailable`, `invalid_volume`,
  `invalid_mute`, `input_release_failed`).
* One new persisted map, `SENDSPIN_SOURCE_INPUT_STATE`, hidden from export like
  `PLAYER_STATE` and preserved across settings saves, imports and the add-on
  translation.
* The Devices page partitions cards by mode; counts, the filter threshold and
  the "no players" empty state follow the section a card is in.
* A bridge older than this decision shows no switch and no level: the SPA
  tolerates the missing type, as it already does for `hardware`.
* The level is a peak, not a true meter: it answers "is anything arriving", and
  is deliberately not calibrated in dBFS.
