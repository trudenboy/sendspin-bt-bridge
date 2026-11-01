# Phone as an audio source on HAOS: is the add-on able to do it, and what already exists

Date: 2026-11-01 · Scope: the `#563` Bluetooth source, HA add-on mode (`ha-addon/`), live HAOS host ·
Code base: `v3` @ `594a491b` (3.2.0-beta.1) · Status: research only, nothing implemented from it.

## TL;DR

1. **The feature exists in this repo, but only for PipeWire.** `services/source/bt_source.py`
   records the phone with `pw-record --target bluez_input.<MAC>.<n>`, finds the node with
   `wpctl status`, and `services/source/relay.py` plays it to a speaker with
   `pw-record | pw-play`. All three tools are PipeWire tools.
2. **HAOS has no PipeWire.** The live host runs **PulseAudio 17.0** as the `hassio-audio`
   service, reachable from add-ons at `/run/audio/pulse.sock`, with
   `module-bluetooth-discover` and `module-bluez5-discover` loaded. PulseAudio registers the
   A2DP endpoints on the adapter itself, so a phone that connects becomes *PA's* card, not
   ours. Per PulseAudio's documentation, when PA takes the A2DP sink role it **creates a
   source** that applications can record from — `bluez_source.<MAC>.a2dp_source`.
3. **The current code refuses HAOS before it even looks.** `capabilities.probe_capture()` is
   literally `shutil.which("wpctl")`, and `application/source.py` turns a false into
   `422 capture_unavailable` ("PipeWire's wpctl is not available"). The hardware model
   already *recognises* `bluez_source.<MAC>` as a `pulse_source` (`inventory.py`), but only
   for display: nothing consumes that fact.
4. **So the add-on can do it — by using the host's PulseAudio instead of PipeWire.** No new
   daemon, no second A2DP stack, no conflict with the speaker outputs (they stay on the same
   PA). The work is a second capture/relay backend plus a capability probe that asks the
   audio server for its name instead of shelling out to `wpctl`, not a new architecture.
5. **No mature "phone → Home Assistant over Bluetooth" project exists.** The exact attempts
   are stubs or unverifiable single-commit repos; the only working code in this space is
   MA-facing (`bluealsa2sendspin`). The nearest maintained neighbour is a *speaker* add-on
   (`scyto/ha-bluetooth-audio-manager`, 80★), i.e. the opposite direction.
6. **One step is still unverified here:** a phone connecting and PA creating
   `bluez_source.*`. No phone was reachable from this host, and a Linux machine acting as a
   synthetic source was rejected at pairing (`org.bluez.Error.AuthenticationRejected`).
   Everything up to that point is confirmed; the last hop needs ten minutes with a real phone.
7. **Side finding, unrelated to HAOS:** `services/source/pairing.py:173` hardcodes the
   production speaker MAC `FC:58:FA:EB:08:6C` in the "trust what just paired" filter. That is
   a defect on any deployment.

## 1. What the repo does today

| Piece | Implementation | HAOS-usable |
|---|---|---|
| Find the capture node | `wpctl status` → `bluez_input.<MAC>.<n>` regex (`bt_source.find_bluez_input_on_host`) | ❌ no `wpctl` |
| Record | `pw-record --target <node> --rate 48000 --channels 2 --format s16 -` (`PwCapture`) | ❌ no `pw-record` |
| Relay to a speaker | `pw-record … \| pw-play --target <sink>` (`relay._command`) | ❌ no `pw-play` |
| Sendspin stream | `aiosendspin.server` + 10 ms PCM chunks (`BtSendspinSource._pump`) | ✅ backend-agnostic |
| Pairing window | `bluetoothctl` over the host's D-Bus + a `PairingAgent` | ✅ `host_dbus: true` |
| Capability gate | `shutil.which("wpctl")` → `422 capture_unavailable` | ❌ always false |
| Hardware facts | `bluez_source.<MAC>` already matched as `pulse_source` | display only |

The docstring of `bt_source.py` explains the PipeWire choice: "PulseAudio's compatibility
layer never exports the phone's capture node as a source (`pactl list sources` shows nothing
for it)". That statement is true for **pipewire-pulse** (the compat layer), and it is exactly
why the choice was made. It does not hold for a native PulseAudio host, which is what HAOS is.

Supporting detail: the add-on image already ships the PulseAudio client tools (`pulseaudio`
in the root `Dockerfile`), and `sync_tuning/bed_stream.py` already spawns `pacat` for the
calibration tone. So `pacat --record` needs no new dependency.

## 2. What HAOS actually provides (live evidence, 2026-11-01)

Read from the host through the SSH add-on (`ssh haos`), which shares the host's D-Bus and the
Supervisor audio socket:

```
$ pactl info
Server String: unix://run/audio/pulse.sock
Server Name: pulseaudio
Server Version: 17.0
Default Sample Specification: s16le 2ch 44100Hz

$ pactl list modules short
6  module-bluetooth-discover
7  module-bluez5-discover
13 module-null-sink  sink_name=sendspin_fallback rate=44100 channels=2 …

$ pactl list sources short
1  sendspin_fallback.monitor  module-null-sink.c  s16le 2ch 44100Hz  IDLE

$ bluetoothctl --version
bluetoothctl: 5.86
$ bluetoothctl list
Controller 00:E0:4C:4A:4A:9A haos #1 [default]
Controller 00:15:83:FF:8F:2B haos
```

Facts that matter:

- **The audio server is a single, host-wide PulseAudio.** `sendspin_fallback` is created by
  the bridge *inside its container* and visible to every other client, so the add-on has
  full read/write access to the same object namespace: it can list sources, record, play,
  set card profiles, and load modules.
- **Bluetooth audio endpoints are PA's, not BlueZ's.** `module-bluez5-discover` is loaded;
  this is the module that registers the A2DP sink/source endpoints on every adapter and
  creates the cards. The bridge never registers endpoints and must not start doing so on
  HAOS — a second endpoint would compete with PA for the phone and, worse, for the speakers.
- **Both adapters are present and idle** (no cards, no sinks but the fallback). `hassio-audio`
  runs in system mode, with the add-on's anonymous socket (`auth-anonymous=1`), so there are
  no per-client ACLs on sources.
- `module-bluetooth-policy` is **not** loaded. That module is what auto-switches the card
  profile on an incoming stream; without it, the profile may need an explicit
  `pactl set-card-profile <card> a2dp-source`. This is a nudge the add-on can perform, and
  it is the main behavioural difference from a desktop PA.
- The add-on manifest already has everything needed (`ha-addon/config.yaml`):
  `host_network: true`, `host_dbus: true`, `audio: true`, `privileged: [NET_ADMIN, NET_RAW]`.
  `entrypoint.sh` exports `PULSE_SERVER=unix:/run/audio/pulse.sock` when the socket exists.

The same topology is what `scyto/ha-bluetooth-audio-manager` (see §5) uses in production to
create *speaker* sinks, which is the mirror image of the phone case.

## 3. PulseAudio as an A2DP sink, from the source

PulseAudio's own documentation states the direction explicitly:

> A2DP: Source role - the device that sends audio. Sink role - the device that receives
> audio. […] PulseAudio supports A2DP in both source and sink roles. When PulseAudio acts
> in the source role, a sink is created where applications can play to. **When PulseAudio
> acts in the sink role, a source is created where applications can record from.**
> — [PulseAudio Bluetooth docs](https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/Bluetooth/)

In the module itself the card profile named `a2dp-source` is the one that creates a recording
source (`module-bluez5-device.c`, `get_profile_direction()`: `A2DP_SINK → PA_DIRECTION_OUTPUT`,
`A2DP_SOURCE → PA_DIRECTION_INPUT`), which is what you see as
`bluez_source.<MAC>.a2dp_source` in `pactl list sources`.

Two consequences for the design:

1. **The capture side is plain PA recording.** `pacat --record --device=bluez_source.<MAC>.a2dp_source
   --rate=48000 --channels=2 --format=s16le` produces exactly the PCM the Sendspin server
   already consumes. Sample-rate conversion is PA's job (the phone's SBC stream is 44.1 or
   48 kHz; the card's default is 44.1 kHz).
2. **The relay side is plain PA playback.** The speaker sinks are
   `bluez_sink.<MAC_underscores>.a2dp_sink` (HAOS naming, confirmed elsewhere in this repo),
   so `pacat --playback --device=<sink>` replaces `pw-play --target <sink>`.

What does *not* transfer from the PipeWire implementation is node naming and discovery:
`bluez_input.<MAC>.<n>` does not exist, and `wpctl` cannot be used as the capability probe.
The node address there is a numeric PipeWire id that WirePlumber renumbers; the PA source name
is stable and MAC-derived, which is actually simpler.

## 4. Options for add-on mode

| Option | How the phone is received | Verdict |
|---|---|---|
| **A. Host PA source (recommended)** | `hassio-audio` PA is the A2DP sink; add-on records `bluez_source.*` with `pacat`, plays to `bluez_sink.*` with `pacat` | ✅ no new daemon, no endpoint conflict, same PA that already drives the speakers. Needs a PA backend + profile nudge |
| B. BlueALSA inside the add-on | `bluealsa` registers its own A2DP sink endpoint and exposes `org.bluealsa` PCM | ❌ competes with PA's already-registered endpoint; to give bluealsa the device you would have to unload `module-bluez5-discover`, which also kills every speaker sink |
| C. Own PA/PipeWire inside the add-on | A private audio server on the host's D-Bus and adapter | ❌ same endpoint conflict, plus a second audio server in a container that already talks to the host's; breaks the speaker path |
| D. AirPlay / Snapcast | Shairport Sync (AirPlay) or Snapcast + a pipe source | ⚠️ works and is well-trodden, but it is not Bluetooth: the phone must use AirPlay, and there is no Sendspin source role. A different product decision |

Option A keeps the current architecture: the phone's PCM enters the same `_pump()` loop and
the same `aiosendspin` server, so remote players and the local relay behave identically on
PipeWire and PA hosts.

## 5. Prior art: what exists, and whether it helps

Searched GitHub (repository search), HACS's default integration list, and the Home Assistant
community add-ons repository on 2026-11-01.

| Project | What it does | Relevance | State |
|---|---|---|---|
| [arthurbre/bluealsa2sendspin](https://github.com/arthurbre/bluealsa2sendspin) | BlueALSA A2DP sink → **Sendspin source** client for Music Assistant, explicitly "without PipeWire or PulseAudio"; maps PCM `Running` to `line_sense` | **Closest architecture to ours.** Proves the "capture without PipeWire" design and the `aiosendspin` source role; it is MA-facing, not an HA add-on | 1★, active (pushed 2026-10-18) |
| [scyto/ha-bluetooth-audio-manager](https://github.com/scyto/ha-bluetooth-audio-manager) | HAOS add-on that pairs/connects **speakers**, exposes each as a PA sink + optional MPD `media_player`; idle modes, AVRCP, AppArmor | **Proof of the add-on pattern** (`host_dbus`, `audio: true`, NET_ADMIN/NET_RAW, ingress) and of host-PA cooperation; opposite direction (no source role) | 80★, active (2026-10-20) |
| [nathan-osman/ha-bluetooth-audio-receiver](https://github.com/nathan-osman/ha-bluetooth-audio-receiver) | Intended: "receive Bluetooth A2DP audio and pipe it to Snapcast" | **Exactly the concept, but a skeleton**: README `TODO`, one commit, no capture code — only `config.yaml` and s6 units for a private dbus + pulseaudio; no `host_dbus` | 0★, created 2025-11-10 |
| [spikeeee/ha-bluetooth-audio-server](https://github.com/spikeeee/ha-bluetooth-audio-server) | HACS integration claiming A2DP sink + multi-room + per-speaker delay + Lovelace card | **Claims the whole feature; not viable on HAOS.** Its own code says "Requires the bluealsa daemon running on the host"; it imports `pydbus` inside HA Core and needs host D-Bus, which a Core container does not have. Single commit, manifest 0.1.0 vs README 1.0.0 | 0★, one commit 2026-05-13 |
| [evzone/hassio-bluepiaudio](https://github.com/evzone/hassio-bluepiaudio) | Notes for Bluetooth audio on **Home Assistant Supervised**: install `bluealsa` + MPD on the host | Shows the host-level bluealsa recipe; incompatible with HAOS's appliance model (no host root, no apt) | 63★, 2020, unmaintained |
| [v3rm0n/addon-shairport-sync](https://github.com/v3rm0n/addon-shairport-sync) | AirPlay 2 receiver add-on | Phone → HA, **over AirPlay**, not Bluetooth; the mature non-BT alternative | 32★, last push 2021 |
| [parautenbach/hass-shairport-sync](https://github.com/parautenbach/hass-shairport-sync) | HACS `media_player` for a Shairport Sync instance (MQTT) | The control-plane half of the AirPlay route; does not capture anything itself | 122★, active (2026-10-31) |
| [nicokaiser/rpi-audio-receiver](https://github.com/nicokaiser/rpi-audio-receiver) | Raspberry Pi as BT A2DP + AirPlay 2 + Spotify Connect receiver | The canonical non-HA recipe (bluealsa + shairport + librespot), useful as a reference for packaging | 1.6k★, active |
| [Art-Ev/addon-snapserver](https://github.com/Art-Ev/addon-snapserver) | Snapcast server add-on | Distributing the captured PCM is a solved problem; it still needs a BT capture source feeding it | 28★, 2025-03 |
| [vojjov/Bluetooth-Audio-Manager-HA](https://github.com/vojjov/Bluetooth-Audio-Manager-HA) | HA + Ubuntu audio VM for MA/Snapcast → BT speakers | Opposite direction, VM-shaped | 0★, one commit 2026-07-09 |

Home Assistant's **community add-ons repository** has no Bluetooth audio add-on (only
`aircast`, `airsonos`, `spotify` in that space), and HACS's default integration list has no
"Bluetooth audio input" integration. HA Core itself has no audio-input concept: it can send
audio to `hassio-audio` but never receives it.

**Verdict on prior art:** nobody has shipped a working, maintained, HA-only "phone over
Bluetooth becomes a source in HA". The two projects that claim it are a stub and a
single-commit integration that cannot run on HAOS. The one piece of working code —
`bluealsa2sendspin` — chose BlueALSA precisely because it avoids both PipeWire and
PulseAudio, which is the right call for a *standalone* client but the wrong one inside an
add-on whose host already runs PA with the endpoints registered.

## 6. What was tested, and what was not

Tested on the live HAOS host:

- PA is 17.0, host-wide, with `module-bluez5-discover`; the add-on can create objects in it
  (the bridge's `sendspin_fallback` null sink is there) and read its sources.
- Both adapters are visible to `bluetoothctl` over the host's D-Bus, and
  `discoverable`/`pairable` can be toggled from an add-on — the pairing-window mechanism works.
- Turning the adapters discoverable/pairable and restoring them afterwards changed nothing
  else (no cards were created, no devices left paired).

Not tested:

- **A phone actually connecting and producing `bluez_source.*`.** No phone was reachable.
  Using a Linux desktop as a synthetic A2DP source failed at pairing
  (`org.bluez.Error.AuthenticationRejected`) — likely the host's own agent policy, not a
  capability problem, but it means the last hop is inference from PA's documentation, not an
  observation. Ten minutes with a phone settles it.
- The profile nudge (`pactl set-card-profile <card> a2dp-source`) on a real phone card.

## 7. Risks and open questions

1. **Profile selection without `module-bluetooth-policy`.** On a desktop PA switches the card
   profile automatically; `hassio-audio` does not load that module. The add-on should set
   `a2dp-source` explicitly when the phone's card appears, and re-assert it if the phone
   reconnects. If the card is created at `off`, nothing will stream and no source will appear.
2. **Sample rate and resampling.** `hassio-audio` defaults to 44.1 kHz; the phone may send
   44.1 or 48 kHz SBC. Recording at 48 kHz makes PA resample; recording at the source rate
   avoids it but complicates the fixed-format Sendspin stream. Measure the added latency
   before choosing.
3. **Adapter bandwidth.** A2DP source (phone) and one or two A2DP sinks (speakers) on the same
   controller share the ACL bandwidth. The production host has two adapters; keeping the
   phone on the adapter with fewer speakers is an operational rule, not a code fix.
4. **System-mode PA and anonymous clients.** The add-on can record any source; that is also a
   security consideration worth one line in the docs (any add-on on the host can do the same).
5. **Trust/agent lifetime.** The pairing agent is unregistered when the source stops; a phone
   paired in the window is trusted afterwards (`pairing.py`), which is what makes reconnects
   work without an agent. That logic is backend-independent.
6. **The hardcoded MAC** in `pairing.py:173` should be removed before this ships to anyone
   else's host.

## 8. Recommendation

Ship option A as a backend behind the existing seams, in this order:

1. `services/source/capture.py`: a small protocol (`find_capture()`, `record_cmd()`,
   `play_cmd()`) with a PipeWire implementation (today's code, unchanged) and a PulseAudio
   implementation (`pactl list short sources` → `bluez_source.<MAC>.*`, `pacat --record`,
   `pacat --playback`).
2. Capability probe: ask `pulse.get_server_name()` (already exists) instead of `which wpctl`,
   and report `capture.available` when **either** backend can work. The 422 message then names
   the right missing piece.
3. Profile nudge on card appearance, with the card name from `pactl list cards short`.
4. Tests: fake `pactl`/`pacat` output for discovery/classification (the `_BLUEZ_SOURCE_RE`
   classification test already exists), and an integration test against the demo's PA-less
   stand is not possible — cover the command construction, not the audio.
5. Docs: one paragraph in `hardware.mdx` that HAOS records through PulseAudio and Docker/Pi
   hosts through PipeWire; the UI copy stays the same ("Phone over Bluetooth").

Scope: one implementation stage on the existing E-plan, no API/UI changes, no migration.
The alternative (AirPlay via Shairport) solves a different product need and should not be
mixed into this feature.

## Sources

- PulseAudio Bluetooth documentation (roles, profile behaviour):
  <https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/Bluetooth/>
- PulseAudio `module-bluez5-device.c` (`get_profile_direction`, profile table):
  <https://gitlab.freedesktop.org/pulseaudio/pulseaudio/-/blob/master/src/modules/bluetooth/module-bluez5-device.c>
- This repo: `services/source/{bt_source,relay,pairing}.py`, `services/hardware/{inventory,capabilities}.py`,
  `application/source.py`, `ha-addon/config.yaml`, `Dockerfile`, `entrypoint.sh`.
- Live host: `ssh haos`, commands and outputs quoted in §2 (2026-11-01).
- Projects: links in the §5 table (stars and last-push dates read from the GitHub API on 2026-11-01).
