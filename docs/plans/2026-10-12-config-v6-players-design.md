# Config schema v6: `players[]` and the `pulse_sink` backend — design

Status: design approved in conversation 2026-10-12, awaiting spec review.
Closes the last open item of v3 stage 5 (see
[v3 plan](2026-10-09-v3-api-first-rewrite.md),
[ADR-0001 §5](../adr/0001-api-contract-and-framework.md)).

## Goal and decisions

Schema v6 makes a configured player independent of Bluetooth, so the bridge can
drive other audio outputs. This cycle ships the schema **and** one non-Bluetooth
backend, `pulse_sink`: any local PulseAudio/PipeWire sink (USB DAC, HDMI,
analogue out) as a Music Assistant player.

Decisions taken while designing:

| Question | Decision |
|---|---|
| Purpose of v6 | Room for non-Bluetooth players; `players[]` with a `backend` discriminated by `type` |
| Record shape | Common player fields + nested `backend` (Pydantic discriminated union) |
| Volatile per-player state | Separate `PLAYER_STATE` keyed by player id, not inside `players[]` |
| HA add-on `bluetooth_devices` option | Migrated once, then ignored and removed from the add-on schema; `config.json` is the only source of truth |
| Rollback to 2.x | Not supported. v5 → v6 is one-way |
| Scope | Schema + `pulse_sink` backend in this cycle |

Out of scope: rollback support, writing both formats, combine sinks, 12 V
triggers, HID buttons, remap sinks and card profiles (backlog), replacing the
Python daemon (see "Prior art").

## 1. Schema v6 and migration

### File layout

```json
{
  "CONFIG_SCHEMA_VERSION": 6,
  "players": [
    {
      "id": "0d5c…",
      "name": "ENEBY20",
      "enabled": true,
      "listen_port": null,
      "static_delay_ms": 0,
      "static_delay_source": null,
      "static_delay_codec": null,
      "static_delay_calibrated_at": null,
      "min_buffer_ms": null,
      "required_lead_time_ms": 250,
      "volume_controller": "pa",
      "idle_mode": "power_save",
      "power_save_delay_minutes": 1,
      "idle_disconnect_minutes": 0,
      "keep_alive_method": "infrasound",
      "room_id": null,
      "room_name": null,
      "backend": {"type": "bluetooth_a2dp", "mac": "FC:58:FA:EB:08:6C", "adapter": "hci0"}
    },
    {
      "id": "b2e0…",
      "name": "DX5 II",
      "volume_controller": "sendspin",
      "idle_mode": "power_save",
      "backend": {
        "type": "pulse_sink",
        "sink": "alsa_output.usb-FiiO_DX5II_ABC123-00.analog-stereo",
        "match": {
          "serial": "FiiO_DX5II_ABC123",
          "bus_path": "pci-0000:00:14.0-usb-0:3:1.0",
          "vendor_id": "2972",
          "product_id": "0047",
          "alsa_long_card_name": "FiiO DX5II at usb-0000:00:14.0-3, high speed"
        }
      }
    }
  ],
  "PLAYER_STATE": {
    "0d5c…": {"volume": 45, "sink": "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", "released": false}
  }
}
```

All other v5 top-level keys stay as they are.

### Rules

* **`id`** is required and immutable. A Bluetooth player gets
  `uuid5(NAMESPACE_DNS, mac.lower())` — the same id Music Assistant knows
  today, so migration creates no new MA players; a Bluetooth player added in v6
  gets the same derivation, so re-adding a speaker does not either. A
  `pulse_sink` player gets `uuid4`. The id never changes afterwards, even when
  the backend's sink name does.
* `player_name` becomes `name`.
* `mac` and `adapter` move into `backend`. `keep_alive_method: infrasound` and
  `idle_mode: auto_disconnect` are valid only for `bluetooth_a2dp`.
* `LAST_VOLUMES[mac]` → `PLAYER_STATE[id].volume`, `LAST_SINKS[mac]` →
  `PLAYER_STATE[id].sink`, the device record's `released` →
  `PLAYER_STATE[id].released`. `LAST_VOLUMES`, `LAST_SINKS` and
  `BLUETOOTH_DEVICES` are removed.
* Legacy `keepalive_enabled` / `keepalive_interval` map to `keep_alive_method`
  if the v5 step has not done it, then are dropped.
* Records without a MAC are dropped with a warning (today they give a player on
  the default sink; `pulse_sink` replaces that case). Duplicate MACs: the first
  record wins, as the validator does today.
* `pulse_sink` players default to `volume_controller: sendspin`. On HAOS the
  output sink is shared by every add-on, so the bridge must not move its level
  unless the user picks `pa` explicitly.

### Where it runs

A new step in `config/migration.py` after the existing v1…v5 steps, run by
`load_config()` and persisted (`needs_persist`). Running it on a v6 file is a
no-op; a file with schema 7+ is left untouched with a warning. Before the first
v6 write the original file is copied to `config.v5.bak.json` — a safeguard for
migration bugs, not a rollback path.

The rest of the code stops reading `BLUETOOTH_DEVICES` dicts: about 20 modules
switch to one accessor, `get_players(config) -> list[Player]`, returning the
Pydantic models.

### Home Assistant add-on

`translate_ha_config.py` copies `bluetooth_devices` from `options.json` into
`players[]` once, while `config.json` has no `players` yet; afterwards the option
is ignored (one warning per start if it is still non-empty). The option is
removed from the add-on schema; old installs keep it in `options.json`, which the
Supervisor leaves alone. `persist_device_enabled` / `persist_device_released`
stop writing to `options.json`.

## 2. API v1 and SPA

`/devices/{device_id}` already addresses players by their stable id; paths do
not change. `PUT /config` stays the single write path for players (the SPA reads
the config, edits the list and saves; `config_diff` → `reconfig_orchestrator`
apply it without a restart).

### Config model (`GET/PUT /config`)

* `BridgeConfig.BLUETOOTH_DEVICES` is replaced by `players: list[Player]`, with
  `Player.backend: Annotated[BluetoothBackend | PulseSinkBackend,
  Field(discriminator="type")]`.
* The models are written by hand (ADR-0001 §5); `schema.json` and OpenAPI are
  generated from them, reversing today's direction.
* `PLAYER_STATE` is internal, like `SECRET_KEY`: not in `GET /config` or
  `/config/export`, not accepted by `PUT /config` (the server keeps its copy).
* `id` is optional in `PUT /config` for new players; the server assigns it.
  For an existing id, changing `backend.type`, `backend.mac` or `backend.match`
  is `422 player_backend_immutable` (replacing a speaker is remove + add).
  Changing `backend.sink` alone is allowed: it re-targets the output.
* `POST /config/import` accepts v5 and v6 files; v5 goes through the same
  migration.

### Devices

* `Device` gains `backend: {type, mac?, adapter?, sink?}` and
  `capabilities: list[str]` from `volume`, `transport`, `power_save`,
  `reconnect`, `repair`, `standby`, `claim`, `pairing_window`, `rssi`,
  `battery`. A `pulse_sink` player has `volume`, `transport`, `power_save`.
* `Device.bluetooth` is `null` for `pulse_sink` (a change for v1 clients,
  acceptable during the beta).
* A command outside `capabilities` answers `409 not_supported_for_backend`,
  checked in one place rather than per use case.
* `DisabledDevice` gains `backend.type`; its `mac` becomes optional.

### New: `GET /audio/sinks`

Local PulseAudio/PipeWire sinks except `bluez_*` and `sendspin_fallback`:
`{name, description, state, sample_spec, identifiers, in_use_by}`, where
`identifiers` is the `match` set and `in_use_by` a player id or null. Fast
`pactl` call in the executor; no job.

### SPA

Regenerated TS client; `stores/bluetooth.ts` and `settings/*` move from
`BLUETOOTH_DEVICES` to `players`. "Add speaker" becomes "Add player" with two
tabs: Bluetooth (today's scan → pair → add) and Audio output (pick from
`GET /audio/sinks`). Cards and menus hide actions not in `capabilities`; RSSI
and battery only for Bluetooth. Settings show adapter, keep-alive and
auto-disconnect only for `bluetooth_a2dp`, and the output for `pulse_sink`.

The compat router for the HA custom component exposes neither MACs nor the
config layout; its shapes stay byte-compatible, and `pulse_sink` players appear
there as ordinary devices without Bluetooth fields.

## 3. `pulse_sink` at runtime

### Activation

`activate_device()` becomes `activate_player(player)` and picks a factory by
`backend.type`:

* `bluetooth_a2dp` — today's path (`BluetoothManager`, MPRIS, RSSI,
  `_on_sink_found`), fed from the model instead of a dict.
* `pulse_sink` — `SendspinClient` without `bt_manager`,
  `bluetooth_sink_name = backend.sink`, daemon spawned with
  `PULSE_SINK=<sink>`, but **only once the sink exists**. Without it the player
  reports `audio.sink_missing` and no daemon runs, so PulseAudio cannot route the
  stream to `sendspin_fallback` or someone else's output.

No `AudioBackend` ABC while there are two backends.

### Finding the sink (from Multi-SendSpin's `DeviceMatchingService`)

On start and on every sink `new` event, a `pulse_sink` player resolves its sink
in this order, logging the method that matched:

1. `device.serial`
2. `device.bus_path` (stable per USB port)
3. `device.vendor.id` + `device.product.id`, only when exactly one sink has
   them; with several, narrowed by `alsa.long_card_name`
4. the stored `backend.sink` name

If the name differs from `backend.sink`, the config is updated and a
`sink_rematched` event is published. No match → `sink_missing`; an ambiguous
vendor:product without a tie-breaker → `sink_ambiguous` (no guess). A sink
claimed by two players goes to the first in list order; the other reports
`sink_in_use`.

### Hot-plug

`SinkMonitor` is keyed by player id instead of MAC (Bluetooth players register
under their id; `extract_mac_from_sink` stays for logs). `pulse_sink` players
add presence callbacks on the `new` / `remove` events it already receives:

* `remove` — reuse the Bluetooth standby path: `SetStandby(sink=sendspin_standby)`
  to the daemon, move its streams to the null sink. The daemon stays connected,
  so the player stays in its MA groups, silent; `audio.sink_missing = true`,
  event `sink_lost`.
* `new` matching the player → `SetStandby(sink=<sink>)`, streams moved back,
  event `sink_restored`.
* Without `pulsectl_asyncio`: poll `pactl list sinks short` every
  `BT_CHECK_INTERVAL`, like the Bluetooth polling fallback.

### Sink health (from local-audio's checks)

Guidance warnings for a `pulse_sink` player's sink:

* muted or at 0 % — "Music Assistant volume cannot raise it; it is the output's
  level";
* active port reported `not available` (e.g. headphones unplugged on a card with
  jack detection). `availability unknown` is not a warning.

The parser is tested against captured `pactl list sinks` output.

### Volume, idle modes, other features

Volume uses the existing controllers; `pulse_sink` defaults to `sendspin`
(software gain). Startup volume comes from `PLAYER_STATE[id].volume`.
`VOLUME_VIA_MA` / `MUTE_VIA_MA` behave the same for both backends.

| `idle_mode` | `bluetooth_a2dp` | `pulse_sink` |
|---|---|---|
| `default` | as today | nothing |
| `power_save` | suspend PA sink | same suspend |
| `keep_alive` | infrasound / silence / none | silence / none |
| `auto_disconnect` | standby + Bluetooth disconnect | rejected by validation |

Optional startup sink reset for `pulse_sink`: one suspend → resume on start,
which clears the kernel regression where an ALSA sink reports RUNNING while no
audio reaches the DAC (Multi-SendSpin `SinkResetService`; only at startup, not
on hot-plug). Off by default.

`pulse_sink` has no churn isolation, reconnect monitor, MPRIS/AVRCP, RSSI,
battery, A2DP dance or cross-bridge duplicate check — all hang off `bt_manager`,
which is `None` for these players.

### Limits

In the add-on only the sinks Home Assistant's audio service exposes are
available (a USB DAC on the HAOS host appears as `alsa_output.*`); in Docker and
LXC, whatever the host's PulseAudio/PipeWire offers. PulseAudio still resamples
to the sink's `sample_spec`, so this is not bit-perfect (#526). Stream formats
are unchanged in stage 3; stage 4 widens them.

## 4. Errors and edge cases

### Migration

* Malformed `BLUETOOTH_DEVICES` entries are skipped with a warning; start-up
  continues.
* If the migration raises, the file is **not** rewritten; the bridge starts with
  an empty `players[]` and guidance `config_migration_failed` carrying the error.
* Both `players` and `BLUETOOTH_DEVICES` present: `players` wins,
  `BLUETOOTH_DEVICES` is dropped with a warning.

### `PUT /config` (RFC 9457 problems with a stable `code`)

* `422 player_backend_immutable` — backend type, MAC or `match` changed for an
  existing id.
* `422 duplicate_player` — two players with one MAC or one sink.
* `422 option_not_supported_for_backend` — `auto_disconnect` or `infrasound` on
  `pulse_sink`.
* No `sink_not_found` on save: the device may be unplugged; the player is saved
  and reports `sink_missing`.

### Runtime

`sink_missing`, `sink_ambiguous`, `sink_in_use` as above; monitor failure falls
back to polling with one warning; `409 not_supported_for_backend` for commands.

## 5. Testing (red/green per `CLAUDE.md`)

* **Migration:** v5 fixtures — plain, with `LAST_VOLUMES`/`LAST_SINKS`, with
  `released`, legacy keep-alive keys, malformed entries, duplicates. Assert the
  id equals `_player_id_from_mac` bit for bit, `PLAYER_STATE` filled, v5 keys
  gone, second run a no-op, schema 7+ untouched. Golden test: the anonymised
  production config (six speakers) in, the expected v6 out.
* **Models:** discriminated union, the `422` cases, server-assigned ids, import
  of a v5 file.
* **Add-on:** `translate_ha_config.py` migrates on first start and ignores the
  option afterwards; `persist_*` no longer touch `options.json`.
* **Sink matching:** table over captured `pactl list sinks` output — serial, bus
  path, unique vendor:product, ambiguous vendor:product, name, nothing.
* **Hot-plug:** `SinkMonitor` driven with fake `new`/`remove` events — standby
  and back, `SetStandby` to the daemon, `sink_lost` / `sink_restored` /
  `sink_rematched`; no daemon without a sink.
* **API:** `capabilities` for both types; `409` for Bluetooth commands on
  `pulse_sink`; `GET /audio/sinks` filtering and `in_use_by`.
* **Regression:** the full suite stays green. Tests that build
  `BLUETOOTH_DEVICES` move to a shared fixture helper — a change of input format
  only, not of what they assert.
* **Live:** Playwright against mock sinks (fake cards and sinks in a dev config,
  like Multi-SendSpin's mock hardware) and a real USB DAC where available; the
  production config migrated on a copy from HAOS.

## 6. Stages (one PR into `v3` and one beta each)

1. **Models and migration.** `Player` / backends / `PLAYER_STATE`, the v6 step,
   backup, `translate_ha_config.py`, add-on schema without `bluetooth_devices`,
   `get_players()` and the ~20 modules on it. Runtime still Bluetooth only. The
   riskiest stage, shipped alone so migration bugs surface on the beta.
2. **API v1 and SPA for Bluetooth.** `players` in `/config`, `backend` +
   `capabilities` in `/devices`, `409 not_supported_for_backend`, regenerated
   client, SPA on `players`. No user-visible change.
3. **Basic `pulse_sink`.** Backend and `activate_player()`, `SinkMonitor` by
   player id, matching by `match`, hot-plug via standby, `GET /audio/sinks`,
   "Audio output" tab, `sendspin` volume by default, sink health warnings,
   optional startup reset, mock sinks.
4. **Formats from DAC capabilities** for `pulse_sink` (from Multi-SendSpin's
   `AudioFormatCatalog` / `AlsaCapabilityService`): read `/proc/asound`
   (USB stream files, HDA codec) with the sink's `sample_spec` as fallback;
   advertise best quality first, FLAC at 48 kHz (or nearest) with the device's
   best depth capped at 24-bit; a preferred format moves to the front without
   dropping the rest. Check whether the add-on needs `/proc/asound` mapped.
5. **Side PR:** check that `volume_controller: sendspin` applies a cubic taper
   (PulseAudio's `pa_sw_volume_to_linear` convention, Multi-SendSpin
   `VolumeCurve`); fix if linear. Affects Bluetooth too.

Backlog: remap sinks with card-profile persistence (split a multichannel card
into stereo zones), HID buttons on USB DACs, start/stop events for amplifier
relays via existing webhooks (no shell hooks).

## Prior art reviewed

* [music-assistant/local-audio-addon](https://github.com/music-assistant/local-audio-addon)
  — one `sendspin-cpp-cli` player per Home Assistant host. Taken: shared-sink
  volume rule, sink health checks, `pactl` parsing tests on captured output,
  preferred-format-first advertising. Not taken: shell hooks (needed a wider
  AppArmor profile), mDNS-only server setting.
* [chrisuthe/Multi-SendSpin-Player-Container](https://github.com/chrisuthe/Multi-SendSpin-Player-Container)
  — many USB DACs on one host (C#). Taken: stable device matching, formats from
  DAC capabilities, cubic volume taper, startup sink reset, mock hardware.
  Backlog: remap sinks, card profiles, HID buttons. Not taken: client id hashed
  from the name (a rename makes a new MA player), combine sinks (bypass MA
  sync), 12 V relay boards.
* `sendspin-cpp-cli` (upstream C++ player with ALSA/Pulse/PipeWire backends) is
  a candidate to replace the per-speaker Python daemon later; it needs its own
  investigation. `pulse_sink` stays deliberately small; users with many wired
  zones are better served by Multi-SendSpin, and the docs should say so.
