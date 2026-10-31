# Hardware section: the physical layer as a first-class surface — design

Status: ready to execute (2026-10-31). Target: the v3 line, after 3.1.0-beta.26.
Plan: [`2026-10-31-hardware-section-plan.md`](2026-10-31-hardware-section-plan.md).
Decision record: [`ADR-0002`](../adr/0002-hardware-inventory.md).

## Goal

The bridge already knows a lot about the hardware it drives — adapters, paired
devices, transports, batteries, cards, ports, sinks, the phone that streams into
the Sendspin source — but the UI exposes that knowledge as fragments: two text
inputs inside the configuration form, a modal of raw `bluetoothctl` lines, a
sink dropdown during player creation, and a settings row for the source. None of
those is an entity. An operator asking "why is this speaker silent" has to read
logs.

This design adds a fifth place to the app — **Hardware** (`/hardware`) — that
shows the physical layer on its own terms, and gives it a read model the rest of
the code (diagnostics, guidance, the future `sbb` CLI) can reuse.

Success is measurable:

* an operator can answer, from one page: what hardware exists, which adapter a
  device is on, why an output is silent (port unplugged, profile `off`, sink
  suspended, no audio server), whether the phone is streaming;
* the page never shows "empty" where the truth is "unavailable" or
  "unsupported on this host";
* a speaker is still a speaker: the hardware page does not absorb player
  management, it links to it.

## Non-goals (v1)

* Creating, removing or renaming players — that stays on Home.
* Switching card profiles or ports from the UI (read-only in v1).
* Per-adapter persisted settings (discoverable, pairable, enabled). That needs
  a config-schema change and is a separate decision.
* Replacing `SbTable`, de-duplicating the four Home card implementations, or
  otherwise refactoring the players page.
* Any new config key. v1 introduces none, deliberately: a key costs four edits
  (`DEFAULT_CONFIG`, `config/schema.json`, migration normalization,
  `config_diff` classification) and none of the facts this section shows are
  configuration.

## Decisions

| # | Decision | Why not the alternative |
|---|---|---|
| 1 | The section is a **separate layer**, not a tab on Home, and not a page of the configuration form | A hardware entity exists whether or not a player points at it: a paired phone that is not streaming, a second adapter with nothing on it, a line-in nobody uses. Nesting it under players reproduces today's blind spot |
| 2 | The Bluetooth **link role** (`output` / `input`) is an explicit computed field with evidence, not a naming convention | Today the bridge is an A2DP *sink* to a phone and an A2DP *source* to a speaker, and nothing records which. Inferring from string prefixes is exactly what breaks on PipeWire |
| 3 | Read model is **card → port/profile → sink/source**, plus BT-owned endpoints marked, not duplicated | "Inputs and outputs" cannot be answered with sinks alone, and "port unplugged" cannot be explained without ports |
| 4 | v1 writes: BT lifecycle + safe audio actions (volume, mute, default sink, suspend, resume). Profile/port switching and player-to-sink routing are deferred | Those touch playout and the daemon IPC, where a mistake is audible to everyone |
| 5 | Navigation label **Hardware / Оборудование**, route `/hardware`; `/devices` stays a legacy redirect | `docs/ux-glossary.md` retired the word "Device" for a speaker, and the UX audit diagnosed exactly that synonym soup |
| 6 | Deliverables for this iteration are documents, not code | The section crosses three layers and a public contract; the contract is worth more than a scaffold |
| 7 | Inputs are three kinds — phone capture, local audio sources, and "unavailable" — each with a capability flag | Promising an input the host cannot see, or hiding the only input the product cares about, are both wrong |
| 8 | Five entity types joined by relations; a graph, not a tree | A tree breaks on the first local DAC and on a bond spanning two adapters |
| 9 | Natural keys in URLs, with an explicit kind segment | Synthetic ids need a persistent registry we do not have; the kind segment disambiguates a sink from a source |
| 10 | One aggregate read (`GET /hardware`) plus per-entity detail; a new `hardware` SSE type, subscribed only after a successful probe | Three parallel requests return three different "now"s; extending `status` grows the hottest payload in the app |
| 11 | One process-wide reader with a short TTL and explicit `as_of` / `stale` | Per-request reads mean dozens of blocking round trips; no cache means the page and the event stream disagree |
| 12 | Hardware forgets bonds but does not manage players; forgetting a bond a player uses warns and links to that player. No `force` | Hiding the action breaks bond repair; a `force` flag turns a warning into a click-through |
| 13 | Move the hardware surfaces; `AudioOutputPicker` stays the player-creation entry point; no duplicate screens | Two sources of truth drift within a release |
| 14 | Five vertical stages (E1–E5) | A horizontal "all backend, then all UI" split ships nothing for months |
| 15 | Eight acceptance criteria, each a test or a CI step | "Looks fine" is not a criterion |
| 16 | In scope only the refactors the section forces: `application/source.py`, one owner of adapter state in the UI, `adapter_names` reading through `load_config` | `SbTable` and the Home cards are a separate refactor that must not ride on a feature branch |
| 17 | No feature flag | The section is additive and read-first; a flag costs more than the risk and tends to produce code nobody enables |
| 18 | Artifacts: this design, the plan, ADR-0002, `CONTEXT.md`, the UX glossary, and a docs-site page with its Russian mirror | — |

## Domain model

Five entity types. Relations are edges between them; nothing is nested.

### Controller

One physical Bluetooth adapter.

| Field | Type | Source |
|---|---|---|
| `id` | `hciN` | sysfs `/sys/class/bluetooth/hciN/address`; synthetic `hci{i}` when sysfs is absent |
| `id_synthetic` | bool | true when the `hciN` was invented |
| `mac` | string | canonical address, the key commands use |
| `name` / `alias` | string | `Adapter1.Alias` / `Name`, else `bluetoothctl show` |
| `powered` | bool | `Adapter1.Powered` |
| `present` | bool | kernel list |
| `class_of_device` | string \| null | raw HCI `Read_Class_of_Device`, `null` without `CAP_NET_RAW` |
| `area` | `{id, name}` \| null | `HA_ADAPTER_AREA_MAP`, read-only here |
| `devices` | `mac[]` | bonds whose `adapters[]` include this controller |

Commands take the **MAC**, not `hciN`: `hciN` is not stable when sysfs is
unmounted, and three of the four existing identity ladders disagree about it.

### BluetoothDevice

A paired or known device.

| Field | Type | Source |
|---|---|---|
| `mac` | string | `Device1.Address`, normalized by `DeviceAddress` |
| `name` / `alias` | string | `Device1.Name` / `Alias` |
| `paired`, `bonded`, `trusted`, `blocked` | bool \| null | `Device1`, tri-state: `null` means "BlueZ did not answer" |
| `connected` | bool | `Device1.Connected` (plain bool, not tri-state: the link either exists or it does not) |
| `controller_macs` | `mac[]` | one entry per adapter holding the bond |
| `battery_pct` | int \| null | `org.bluez.Battery1.Percentage` |
| `rssi` | `{delta_db, at}` \| null | kernel mgmt opcode `0x0031`; `at` is the observation time |
| `transport` | `{codec, codec_id, state, delay_ms}` \| null | `MediaTransport1` |
| `roles` | `RoleEvidence[]` | computed, see below |
| `player_id` | string \| null | config cross-reference |
| `source_phone` | bool | `SourceFeed.phone_mac == mac` |

### AudioCard

| Field | Type | Source |
|---|---|---|
| `name` | string | `card_list` name (e.g. `bluez_card.FC_58_…`) |
| `driver` | string | `card_list` driver |
| `active_profile` | string | |
| `profiles` | `{name, description, available}[]` | full profile list; today only the active one is read |
| `ports` | `{name, description, available, active}[]` | full port map; today only the active port and its availability survive parsing |
| `endpoints` | `{kind, name}[]` | sinks and sources belonging to this card |

### AudioEndpoint

| Field | Type | Source |
|---|---|---|
| `kind` | `sink` \| `source` | |
| `name` | string | PA/PipeWire name |
| `description` | string | |
| `owner` | `{card: name}` \| `{bluetooth: mac}` \| `{internal: true}` | classification, not ownership in the object-graph sense |
| `state` | string | `running` / `idle` / `suspended` / `unavailable` |
| `sample_spec` | string | |
| `muted` | bool | sinks only |
| `volume` | int | sinks only, 0–100 |
| `identifiers` | `{serial, bus_path, vendor_id, product_id, alsa_long_card_name}` | for stable references across renames |
| `in_use_by` | string \| null | player id from `players[].backend` |
| `active_port` | string \| null | |

Bluetooth-derived sinks (`bluez_sink.*`, `bluez_output.*`) are **not** listed
in the local outputs group. They appear as the player's link and as
`owner.bluetooth` on the device card. This preserves the current
`GET /audio/sinks` contract, which excludes them on purpose.

### SourceFeed

The single Sendspin source (phone → bridge).

| Field | Type | Source |
|---|---|---|
| `enabled` | bool | `SENDSPIN_SOURCE` + runtime start/stop |
| `port` | int \| null | `SENDSPIN_SOURCE_PORT` |
| `state` | `off` \| `waiting` \| `streaming` \| `error` | `services/source` state |
| `phone_mac` | string \| null | the paired peer currently streaming |
| `capture` | `{kind: pipewire_node \| pulse_source \| unavailable, node}` | `wpctl` / `pactl list sources` |
| `relays` | string[] | player ids armed in `attached_devices` |
| `error` | string \| null | |

### Link role (computed)

`services/hardware/roles.py`, a pure function over the device state, the
endpoint list and the source feed:

| Role | Evidence |
|---|---|
| `output` | an active `MediaTransport1` for this MAC, or a sink named `bluez_sink.<mac>…` / `bluez_output.<mac>…` that is not suspended |
| `input` | a capture node `bluez_input.<MAC>.<n>` or a source `bluez_source.<MAC>`, or `SourceFeed.phone_mac == mac` |

Both roles may be present at once (a multipoint headset, or a phone that also
receives audio). The UI renders one badge per role. `evidence` is part of the
response so the UI never has to guess why a badge is there.

### Identity

* Bluetooth addresses: one parser, `bluetooth/address.py:DeviceAddress`. New
  code never compares address strings; it renders whichever form the consumer
  needs.
* Audio names: one grammar, `services/audio/sink_names.py`. New code does not
  match prefixes by hand.
* URL form: `/hardware/<kind>/{key}` where `<kind>` is one of
  `controllers`, `bluetooth`, `audio/cards`, `audio/sinks`, `audio/sources`.
  Audio names are percent-encoded and validated against
  `^[A-Za-z0-9._:@+-]+$`; anything else is a 400, never a silent miss.
* No synthetic ids: PA names and MACs are stable enough, and a registry that
  survives a restart is a bigger commitment than this section justifies.

## API contract

All reads live under `/hardware`; commands stay on the resource paths that
already exist, so there is exactly one way to do each thing.

### Aggregate

```
GET /api/v1/hardware -> HardwareSnapshot
```

```jsonc
{
  "as_of": "2026-10-31T11:02:13Z",       // ISO-8601, UTC
  "stale_after_s": 2,
  "stale": false,
  "capabilities": {
    "bluetooth": true,
    "dbus": true,
    "audio_server": "pulseaudio",         // "pipewire" | "pulseaudio" | "none"
    "capture": false,
    "default_sink": true
  },
  "controllers": [ControllerSummary],
  "bluetooth_devices": [BtDeviceSummary],
  "audio": { "cards": [CardSummary], "sinks": [EndpointSummary], "sources": [EndpointSummary] },
  "source": SourceFeed | null,
  "degraded": [ { "section": "controllers", "code": "dbus_unavailable", "detail": "…" } ]
}
```

`ControllerSummary` = `id, id_synthetic, mac, name, alias, powered, present,
class_of_device, area, device_count`.
`BtDeviceSummary` = `mac, name, alias, paired, bonded, trusted, connected,
controller_macs, battery_pct, rssi, transport, roles, player_id, source_phone`.
`CardSummary` = `name, driver, active_profile, profile_count, port_count,
endpoint_refs`.
`EndpointSummary` = `kind, name, description, owner, state, sample_spec, muted,
volume, in_use_by, active_port`.

The aggregate grows by stage, and the document stays valid at every stage:
**E1 returns `controllers` and `bluetooth_devices` only** — `audio` and `source`
are absent, not empty. E2 adds `audio`, E3 adds `source`. Until E2 the per-device
`transport` is `null` even when a role is evidenced by `transport:active`: the
role proves a link exists, it is not a codec read. An object filled with empty
arrays would claim "this host has no audio", which is the lie this design exists
to prevent.

### Detail

```
GET /api/v1/hardware/controllers/{id}       -> ControllerDetail      (adds devices[], address forms)
GET /api/v1/hardware/bluetooth/{mac}        -> BtDeviceDetail        (adds uuids, services_resolved, modalias, manufacturer, bonds[])
GET /api/v1/hardware/audio/cards/{name}     -> CardDetail            (adds profiles[], ports[], endpoints[])
GET /api/v1/hardware/audio/sinks/{name}     -> EndpointDetail        (adds identifiers, all ports)
GET /api/v1/hardware/audio/sources/{name}   -> EndpointDetail        (adds identifiers, monitor_of)
```

`BtDeviceDetail.manufacturer` comes from `bluetooth/vendor_map.py`, which
already exists but is used only for internal heuristics.

### Commands

Reused as-is (no copies):

| Command | Endpoint |
|---|---|
| Adapter power | `PUT /adapters/{id}/power` |
| Scan | `POST /bluetooth/scans` (job) |
| Pair | `POST /bluetooth/pairings` (job) |
| Connect a known device | `POST /bluetooth/devices/{mac}/connect?adapter_mac=` |
| Disconnect | `POST /bluetooth/devices/{mac}/disconnect` |
| Forget bond | `DELETE /bluetooth/devices/{mac}?adapter_mac=` |

New in v1 (safe audio only):

| Command | Endpoint |
|---|---|
| Sink volume | `PUT /audio/sinks/{name}/volume` |
| Sink mute | `PUT /audio/sinks/{name}/mute` |
| Default sink | `PUT /audio/default-sink` |
| Suspend / resume a sink | `POST /audio/sinks/{name}/suspend`, `POST /audio/sinks/{name}/resume` |

Deferred (v2): `PUT /audio/cards/{name}/profile`, `PUT /audio/sinks/{name}/port`,
player-to-sink routing.

### Errors

`UseCaseError` with stable codes; clients branch on `code`, never on wording.

| Code | Status | When |
|---|---|---|
| `entity_not_found` | 404 | unknown MAC, card or endpoint |
| `hardware_unavailable` | 503 | the whole section cannot be read (no D-Bus and no `bluetoothctl`) |
| `not_supported_for_backend` | 409 | command on a backend that does not have it |
| `capture_unavailable` | 422 | asked to start the source without a capture node |
| `source_sink_missing` | 409 | asked to play the source on a speaker with no audio output |
| `invalid_audio_name` | 400 | name outside the allowed grammar |
| `bond_in_use_by_player` | 409 | forgetting a bond that backs a player; carries `remediation` and the player id |
| `connect_refused` | 409 | BlueZ refused the connect — out of range, or the bond is gone |
| `bluetooth_unavailable` | 502 | BlueZ could not answer; the verb or the device list is unreadable |

A degraded *section* is not an error: it is `degraded[]` in a 200 response.
Only a request that cannot be answered at all returns 503.

### Events and version skew

A new SSE type `hardware` joins `ALL_TYPES` but **not** `DEFAULT_TYPES`: the
payload is large and only one page needs it. Events are coalesced to at most
one per second.

The stream negotiates by capability, because the server rejects unknown types
with 400 `unknown_event_type` (`api/events.py:_parse_types`) and the SPA always
sends an explicit `types=` list:

1. the UI connects with the default types;
2. it calls `GET /api/v1/hardware` once;
3. on success it reconnects with `hardware` added; on 404/405/500 it stays on
   the 5 s poll and never mentions the type again.

Consequences: a new UI against an old bridge loses live updates, not the event
stream; an old UI against a new bridge never sees the type. Both are covered by
tests. Weakening `_parse_types` to ignore unknown types was rejected: it is a
silent public-contract change made to spare one client a probe.

## Freshness, cost and degradation

Reads are expensive: one `GetManagedObjects` per adapter, one `card_list`, one
`sink_list`, one `source_list`, one `wpctl status`. Ten devices must not mean
ten round trips.

* One process-wide reader (`services/hardware/inventory.py`) with a 2 s TTL and
  a single in-flight lock. Every consumer — the aggregate, the detail endpoints,
  guidance, diagnostics — reads through it.
* The response carries `as_of` and `stale`. When the cached copy is older than
  the TTL because the last refresh failed, the snapshot is still returned, with
  `stale: true` and a `degraded[]` entry. The UI says "data may be stale"
  instead of silently showing old state.
* Read-only D-Bus calls do **not** take the adapter lease. The `bluetoothctl`
  fallback does: it is a blocking command, and the process-wide lease is what
  keeps pairing, scanning and RSSI refresh from colliding. A read that cannot
  get the lease waits; it never bypasses it.
* Demo mode feeds the same shape from `demo/fixtures.py` (see the plan, E1).

| Section | Source of truth | When unavailable |
|---|---|---|
| controllers | D-Bus `GetManagedObjects` on `org.bluez`; sysfs for `hciN` | fall back to `bluetoothctl` *under the lease*; if that fails too, `unavailable` + `dbus_unavailable` |
| bluetooth_devices | `Device1`, `Battery1`, `MediaTransport1`; RSSI from the status bag (`rssi_at_ts`) | devices still listed; battery/RSSI/transport are `null`, never `0` |
| cards, sinks, sources | `pactl` / pulsectl (`card_list`, `sink_list`, `source_list`) | `audio_server: "none"`, section `unavailable` + `pulse_unavailable` |
| source | `services/source` module state | `enabled: false`, state `off` — that is not an error |
| capture | `wpctl` nodes `bluez_input.*`; `pactl` sources `bluez_source.*` | `kind: "unavailable"` + `capture_unavailable`; the phone is still shown as a Bluetooth device |

## UI / UX

### Navigation

Fifth item in `ui/src/router/nav.ts`, icon from lucide, short label for the
phone bottom bar, and a new first segment in `ui/src/router/segments.ts`
(otherwise ingress base-path detection breaks — this is covered by a unit test).
`/devices` keeps redirecting to `/`; existing redirect tests are untouched.

### Screens

One page, three groups plus a strip:

1. **Controllers** — one card per adapter: power state, friendly name, CoD,
   devices on it, area. Actions: power on/off.
2. **Bluetooth devices** — paired and known devices: connected, battery, RSSI,
   role badges, codec, the player it backs (link to Home). Actions: connect,
   disconnect, forget (with the player warning).
3. **Audio** — cards → ports/profiles (read-only), local outputs with
   state/volume/in-use, local inputs with kind and reason. Actions: volume,
   mute, default, suspend/resume.
4. **Source strip** — enabled, state, the phone, the capture node and its kind,
   relays. Actions: start/stop, pairing window.

Entity detail is an `SbDrawer`, not a modal of raw command output. The drawer
has: overview, technical details (behind the existing `technicalDetails`
preference), and actions.

### States and copy

* Empty, unavailable and unsupported are three different screens:
  `SbEmptyState` for "no devices paired", `CapabilityBanner` for "this host has
  no D-Bus" with the reason and the next step, and an inline note for "this
  build cannot see the phone capture, it needs PipeWire nodes".
* The stale badge appears only when `stale: true`.
* All strings go through i18n with a Russian translation; product names and
  MACs are not translated. New words enter `docs/ux-glossary.md` first.

### Actions

Every action is registered in `ACTION_POLICY` (`undo` / `confirm` / `choice`).
Destructive ones use `ConfirmConsequences` and name the affected player. No raw
`confirmDialog()`.

## Layering and files

```
api/routers/hardware.py          thin: Depends(require_principal), PROBLEM_RESPONSES
application/hardware.py          composition, config cross-refs, degraded[]
application/models/hardware.py   Pydantic output models
application/audio.py             + set_sink_volume/mute, set_default_sink, suspend/resume
application/source.py            NEW: moves orchestration out of api/routers/bridge.py
services/hardware/inventory.py   the one reader + TTL cache
services/hardware/capabilities.py
services/hardware/roles.py       pure inference
services/audio/sink_inventory.py + LocalCard, LocalSource, parse_pactl_cards/sources
api/events.py                    + "hardware" in ALL_TYPES
```

The router imports nothing from `services` at module level; `application` never
imports `api`; no blocking call appears in a coroutine (the existing
`test_blocking_facade_rule.py` enforces the last one).

## Alternatives considered

* **Fold the section into Home as a tab.** Rejected: it keeps the player as the
  organising unit, which is the blind spot this work removes.
* **Synthetic entity ids from a registry.** Rejected: needs persistence and
  migration, buys nothing over MAC and PA name.
* **Return everything from `/status`.** Rejected: that payload is built on every
  status change and already carries the bridge, guidance, onboarding and
  recovery; adding a fan-out of blocking hardware reads to it would slow every
  page.
* **Weaken `_parse_types` to ignore unknown types.** Rejected: silent contract
  change; the probe keeps the strictness.
* **Make the source a `backend.type` so it becomes a `Device`.** Rejected for
  v1: the source has no player, no playback and no per-device lifecycle; forcing
  it into `Device` would add `None` branches to every player path. It stays its
  own entity.
* **A feature flag.** Rejected: additive, read-first, and a flag nobody enables
  is dead code.

## Open questions

1. Should `GET /bluetooth/devices/{mac}` (untyped `dict`, raw `bluetoothctl`
   lines) be deprecated in the same release as `/hardware/bluetooth/{mac}`, or
   kept until the HA custom component moves to v1? Default: keep for one
   release, mark deprecated.
2. Should the controller list absorb `AdaptersSection`'s name and CoD fields, or
   keep configuration in Settings and only link? Default: keep editing in
   Settings for v1 (no config-write surface in the new page), link both ways.
3. Does the `sbb` CLI want the aggregate endpoint as-is, or a `--section` flag?
   Default: as-is; the CLI filters client-side.
4. ~~Should `SourceFeed` carry the remaining pairing window?~~ **Resolved
   (2026-10-31) without a new field.** The status stream already carries
   `sendspin_source.pairing_seconds_left` (it is part of `SendspinSourceStatus`),
   so the SPA adopts it on load and the countdown survives a page reload without
   duplicating the value in `SourceFeed`. A bridge restart ends the window with
   the process, so there is nothing to carry across one.
