# ADR-0002 — The hardware inventory is its own read model

* **Status:** accepted (2026-10-31)
* **Date:** 2026-10-31
* **Plan:** [Hardware section: staged plan (E1–E5)](../plans/2026-10-31-hardware-section-plan.md)

## Context

The bridge knows the physical layer it drives: Bluetooth controllers, paired
devices with their bonds, batteries and transports, audio cards with ports and
profiles, sinks and sources, and the phone that streams into the Sendspin
source. None of it has a home.

What exists instead:

* `GET /api/v1/adapters` returns four fields per adapter; the persisted record is
  an entry in the `BLUETOOTH_ADAPTERS` config array (`mac`, `name`, `hci`,
  `device_class`) with no discoverable/pairable state and no adapter-scoped
  device assignment — a bond's adapter is inferred at runtime and reported as a
  bare list.
* `GET /api/v1/audio/sinks` is the only audio-topology endpoint. It lists sinks
  and deliberately excludes Bluetooth and bridge-internal ones. Cards exist
  inside `services/audio/pulse.py` but reach the API only as
  `diagnostics.cards`; ports are parsed down to the active one; sources do not
  exist anywhere in the read model.
* The source is a `SendspinSourceStatus` field on `Bridge` plus two module-level
  dicts in `services/source/`. It is not an entity: no id, no `backend.type`, so
  it never appears in `Device` or `Group`.
* Adapter identity is resolved by four different ladders (sysfs → `hciconfig` →
  D-Bus object path → list position) and callers pick their own.
* In the SPA the same facts surface as two text inputs in the configuration
  form, a modal of raw `bluetoothctl` lines, a sink dropdown during player
  creation, and one settings row for the source.

The consequences are visible to operators: "why is this speaker silent" cannot
be answered from the UI, a paired phone that is not streaming is invisible, and
adapter `powered` badges are fetched once per SSE connect and never again.

`ROADMAP.md` (Epics 5–7) already promises "show discovered hardware with backend
type, friendly naming, and capability hints", hotplug detection for USB DACs,
and "surface route ownership and sink disappearance issues explicitly instead of
burying them in logs". Those promises need a read model, not another page.

## Decision

### 1. A hardware read model, separate from players

Five entity types — `Controller`, `BluetoothDevice`, `AudioCard`,
`AudioEndpoint` (`sink`/`source`) and `SourceFeed` — joined by relations, not
nested. A player is a consumer of an endpoint, referenced by `player_id`; it
never owns hardware.

The Bluetooth link role (`output` / `input`) is computed with explicit evidence
(`MediaTransport1` activity, `bluez_input.*` capture node, `bluez_source.*`
source, or the source feed's phone), because the bridge is simultaneously an
A2DP sink to a phone and an A2DP source to a speaker and nothing records that.

### 2. The read surface is `GET /api/v1/hardware`, with per-entity detail

One aggregate for the page's first paint, plus
`/hardware/{controllers,bluetooth,audio/cards,audio/sinks,audio/sources}/{key}`
for details. Keys are natural (MAC, PA name) with an explicit kind segment; no
synthetic id registry.

Commands stay on their existing resource paths (`/adapters/{id}/power`,
`/bluetooth/scans|pairings|devices/{mac}/…`). Only the missing safe audio
commands are added (`/audio/sinks/{name}/volume|mute`, `/audio/default-sink`,
`/audio/sinks/{name}/suspend|resume`). Exactly one way to do each thing.

### 3. A new `hardware` event type, negotiated by probe

The type joins `ALL_TYPES`, not `DEFAULT_TYPES`: the payload is large and one
page needs it. The SPA asks for it only after a successful `GET /hardware`,
because `_parse_types` rejects unknown types with 400 and a failed subscription
takes the whole stream down. `_parse_types` stays strict: relaxing it would be a
silent public-contract change made to spare one client a probe.

### 4. One process-wide reader with a short TTL

`services/hardware/inventory.py` owns every read: one `GetManagedObjects` per
adapter, one `card_list`, one `sink_list`, one `source_list`, one `wpctl`
status, cached for 2 s with `as_of` and `stale` in the response. Every consumer
— aggregate, details, guidance, diagnostics — reads through it.

*Clarified 2026-11-06:* Source feed status and input selection reuse this
reader too. One hardware response projects its Source feed from the same
inventory and one source runtime snapshot; source discovery has no separate
cache. Player exclusions are applied to cached facts using the current
configuration. An unavailable audio list remains unknown rather than becoming
a confirmed empty list.

Runtime invalidation after bond or audio changes expires the read without
discarding its last successful facts. If refreshing fails, that read remains
available with `stale` and degradation reasons; failed refreshes also respect
the short TTL. A retained stale read cannot confirm a requested profile or
port change as newly observed. Blocking reads run outside the bridge loop;
projection of supplied facts performs no hardware reads.

Read-only D-Bus calls do not take the adapter lease. The `bluetoothctl` fallback
does: it is a blocking command, and the lease is what keeps pairing, scanning
and RSSI refresh from colliding. A read that cannot get the lease waits.

### 5. Degradation is data, not an empty list

`capabilities` and `degraded[]` carry the reason (`dbus_unavailable`,
`pulse_unavailable`, `capture_unavailable`). An empty section means "nothing is
there"; an unavailable section says why and what would change it. The UI renders
them as different screens.

### 6. The source becomes an entity and gets an application module

`SourceFeed` joins the aggregate, and `application/source.py` takes over the
orchestration currently inlined in `api/routers/bridge.py:100-145`. That inlining
is the only live violation of the thin-router rule from ADR-0001.

### 7. Scope guards

In scope only the refactors this decision forces: the source use case, a single
owner of adapter state in the SPA, and `adapter_names` reading through
`load_config()` instead of `config.json`. Out of scope: `SbTable`, the four
Home card implementations, card profile / port switching, player-to-sink
routing, per-adapter persisted settings, and any new config key in v1.

## Alternatives considered

* **A tab on Home.** Keeps the player as the organising unit, which is the blind
  spot this work removes: entities that no player points at would stay invisible.
* **A section of the configuration form.** Configuration is what an operator
  changes rarely; hardware state changes constantly. Mixing save-on-submit with
  live state reproduces the stale-badge problem.
* **Synthetic ids from a registry.** Needs persistence, migration and cleanup;
  MAC and PA name are stable enough and already have parsers.
* **Everything in `/status`.** That payload is built on every status change and
  already carries guidance, onboarding and recovery. Adding a fan-out of
  blocking hardware reads would slow every page in the app.
* **Ignore unknown SSE types server-side.** Silent contract change; the probe
  keeps strictness and only costs one request.
* **The source as a `backend.type`, hence a `Device`.** It has no player, no
  playback and no per-device lifecycle; forcing it into `Device` would add
  `None` branches to every player path.
* **A feature flag.** The section is additive and read-first; a flag nobody
  enables is dead code, and the config plumbing costs four edits.

## Consequences

* The SPA gains a fifth place (`/hardware`) and `ui/src/router/segments.ts` gains
  a segment; `/devices` keeps its legacy redirect, so no existing redirect test
  changes.
* `PairedDevicesList` and `BtDeviceInfoModal` are removed; `AdaptersSection`
  keeps configuration editing and links to the new page; `SourceStatusSection`
  moves out of Settings → Music Assistant.
* `GET /bluetooth/devices/{mac}` (untyped `dict` with raw `bluetoothctl` lines)
  becomes redundant. It is kept for one release and marked deprecated in
  `CHANGELOG.md`; the typed replacement is `/hardware/bluetooth/{mac}`.
* New public surface: `/hardware*`, five `/audio` commands, one SSE type, six
  stable error codes. All of it is additive.
* The reader adds one D-Bus round trip per adapter and one `pactl` read per TTL
  window to the bridge's steady state; the lease is untouched on the happy path.
* Accuracy limits are now explicit: `class_of_device` is `null` without
  `CAP_NET_RAW`, `rssi` is a delta in the BR/EDR golden range rather than dBm,
  and the phone capture may be a PipeWire node, a PulseAudio source, or absent.
* Known defect, not fixed here: two files in `docs/adr/` share number
  `0001`. Renumbering breaks external links for no gain.

## Open questions

1. Deprecate `GET /bluetooth/devices/{mac}` in the same release, or wait for the
   HA custom component to move to v1? Default: one release, deprecation entry
   only.
2. Does adapter name/CoD editing move into the hardware page, or stay in
   Settings? Default: stay in Settings for v1; the page links both ways.
3. Should the future `sbb` CLI consume the aggregate as-is, or ask for sections?
   Default: as-is.
