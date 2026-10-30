---
title: Bluetooth Speakers
description: Bluetooth speakers and headphones tested with Sendspin BT Bridge — confirmed working, working with caveats, and documented quirks
---

This page collects field datapoints on Bluetooth speakers and headphones that have been driven through the bridge. The split is:

- **Confirmed working** — A2DP plays cleanly under typical workloads. Where a specific bridge version, BlueZ version, or experimental toggle was required to reach this state, the dependency is noted.
- **Working with caveats** — Plays, but a non-default option, host upgrade, or workaround is required.
- **Documented quirks (no confirmed fix)** — Reproducibly misbehaves in ways the bridge cannot solve on its own; cited so future readers spot the symptom faster.

Every row links back to the original issue thread so you can read the full diagnostic.

:::tip[Add your speaker]
If your model isn't listed, please [open an issue](https://github.com/trudenboy/sendspin-bt-bridge/issues/new) with the model name, BlueZ version, bridge version, and a `Diagnostics → Download` bundle. Datapoints — positive **and** negative — make this list useful.
:::

## Confirmed working

| Speaker / headphone | Source | Notes |
|---|---|---|
| **IKEA ENEBY20** | Author's daily deployment | A2DP, multiroom; CSR8510 A10 adapter |
| **IKEA ENEBY Portable** | Author's daily deployment | Same family as ENEBY20 |
| **IKEA VAPPEBY** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) (chino-lu, Pi 5 + ASUS USB-BT500) | A2DP in multiroom on BlueZ 5.85 |
| **Yandex Mini 2** | Author's daily deployment | Standby quirks documented in [Troubleshooting](/troubleshooting/) |
| **Lenco LS-500** | Author's daily deployment | A2DP, multiroom |
| **AfterShokz OpenMove** | Author's daily deployment | Bone-conduction headphones; A2DP playback works cleanly while in BT range |
| **HUAWEI FreeClip** (open-ear clip earbuds) | Author's daily deployment | A2DP plays cleanly; treat as a portable headphone — out-of-range drops are normal |
| **Anker Soundcore Sport X10** (sport earbuds) | Author's daily deployment | A2DP plays cleanly; same out-of-range note as other portable earbuds |
| **Jam Heavy Metal** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) | A2DP in multiroom on BlueZ 5.85 |
| **HMDX Jam** | [#166](https://github.com/trudenboy/sendspin-bt-bridge/issues/166) — fixed in v2.60.2 | Needed explicit `Device1.ConnectProfile(A2DP_SINK_UUID)` because the speaker also advertises A2DP source / HFP. Auto since v2.60.2 |
| **IKEA Kallsup** | [#166](https://github.com/trudenboy/sendspin-bt-bridge/issues/166), [#162](https://github.com/trudenboy/sendspin-bt-bridge/issues/162) — fixed in v2.60.2 | Same A2DP-Sink ConnectProfile fallback as HMDX Jam |
| **Xiaomi 小爱音箱 (Mi Speaker)** | [#172](https://github.com/trudenboy/sendspin-bt-bridge/issues/172) — addressed in v2.61.0 | Stale BlueZ disk-cache cleared on remove; re-pair recovers from `BlueZ has no record` |
| **EDIFIER B3 Soundbar** | [#123](https://github.com/trudenboy/sendspin-bt-bridge/issues/123) — v2.55.3 added sink-mute detection | If you ever see "audio plays but no sound", the device card now flags **Sink muted** with a one-click **Unmute** action |
| **Samsung Soundbar M360 M-Series** | [#254](https://github.com/trudenboy/sendspin-bt-bridge/issues/254) | Speaker itself is fine. The original adapter-detection report turned out to be unrelated (an adapter passthrough hiccup during container update) |
| **Anker Soundcore 2 / Soundcore 3** | [#291](https://github.com/trudenboy/sendspin-bt-bridge/issues/291) | Individual speakers work cleanly. Two on the same adapter hit BR/EDR airtime contention — use one adapter per 2–3 speakers (see [Bluetooth Adapters › How many adapters](/bluetooth-adapters/#how-many-adapters-do-i-need)) |
| **Sony STR-DN1080** (network/AV receiver) | [#161](https://github.com/trudenboy/sendspin-bt-bridge/issues/161) | On PipeWire-pulse: raise `pulse_latency_msec` (Rowr21 reached stable sync at 550 ms with the AVR added to the group) |

## Working with caveats

| Speaker / headphone | Source | Requirement |
|---|---|---|
| **Sony WH-1000XM4** | [#269](https://github.com/trudenboy/sendspin-bt-bridge/issues/269) — verified by arisonpl; also in author's deployment | **BlueZ ≥ 5.79** (5.82 verified) **and** bridge ≥ v2.70.0. On BlueZ 5.66 (RPi OS Bookworm) the AVDTP-collision fast-path-skip fires incorrectly; both halves of the upgrade are required. See [Troubleshooting › Reconnect loop on Sony WH-1000XM4](/troubleshooting/#reconnect-loop-on-sony-wh-1000xm4--other-a2dp-sinks-avdtp-collision-on-old-bluez) |
| **Samsung Q910B soundbar** | [#210](https://github.com/trudenboy/sendspin-bt-bridge/issues/210) | Needs the adapter **Class of Device override `0x00010c`** plus an HA restart to clear stuck BlueZ runtime state. The ATS2851 chipset has incomplete Linux support but the CoD workaround unblocks pairing. Listed in [Class of Device override — preset reference](/troubleshooting/#class-of-device-override--preset-reference) |
| **Synergy S65** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) | Works on BlueZ **5.85**. BlueZ 5.86 breaks volume control specifically for this speaker — pin to 5.85 if it's in your group |
| **JBL PartyBox Encore 2 (ENC ESS 2)** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) | **Works alone.** Breaks when added to a multi-speaker group: the JBL "locks" the controller and other speakers drop audio. JBL-specific (TMAP 1.0 / PBP 1.0 / JBL PartyBoost peer modes). Stream solo to this device |

## Documented quirks (no confirmed fix in thread)

| Speaker / headphone | Source | Symptom |
|---|---|---|
| **HK Onyx Studio 3** | [#191](https://github.com/trudenboy/sendspin-bt-bridge/issues/191) | `ServicesResolved did not reach True within 10s` + `A2DP Sink ConnectProfile: UnknownObject`, speaker drops ~3 s after connect. Matches the [bluez/bluez#1098](https://github.com/bluez/bluez/issues/1098) / [#1922](https://github.com/bluez/bluez/issues/1922) regression class. **Reset & Reconnect** from the device card sometimes recovers; no permanent fix landed in the thread |
| **Tribit StormBox 2** | [#355](https://github.com/trudenboy/sendspin-bt-bridge/issues/355) | Pairing fails with `br-connection-profile-unavailable` even from `bluetoothctl` on the host (Proxmox LXC). The speaker has a mic and insists on the HFP (headset) profile first; hosts without an HFP backend can't bring any profile up. See [Troubleshooting › HFP-default speakers](/troubleshooting/#pairing-fails-with-br-connection-profile-unavailable-hfp-default-speakers) |

## How to interpret these tiers

- **Confirmed working** doesn't mean "works on every host." A speaker that's clean on PulseAudio 17 + BlueZ 5.82 might still hit a `pulse_latency_msec` tuning step on PipeWire-pulse, or need an adapter swap if the on-board controller is overloaded. Cross-check against [Bluetooth Adapters](/bluetooth-adapters/) and [Troubleshooting](/troubleshooting/) when in doubt.
- **Working with caveats** entries mean the workaround is **available in the bridge or in the docs** — you don't need a custom firmware build to get there.
- **Documented quirks** is here so a future operator can recognize the fingerprint quickly instead of debugging from scratch. If you find a workaround, please report it back so we can re-tier the speaker.

## Bluetooth sink suspend

When the host suspends a speaker's audio sink in the gap between songs, the speaker's A2DP stream stops and restarts. After such a restart the speaker's buffer can shift by tens of milliseconds, so a group that was in sync before the pause may be slightly off afterwards.

The bridge only **reads** the host's behaviour; it never changes a host audio setting. It raises the guidance card "the host suspends the Bluetooth sink between songs" when it sees the sink go to `suspended` while the speaker was connected and the bridge had not asked for it, or when it finds a suspend policy with a timeout shorter than 60 s (PipeWire's WirePlumber, or PulseAudio's `module-suspend-on-idle`). The card clears after 24 hours without an observed suspend and a clean probe. The probe needs `pactl`; on PipeWire it also needs `pw-dump` and the native PipeWire socket, otherwise only observed suspends raise the card.

To stop the host from suspending the sink, change the host, not the bridge:

**WirePlumber 0.5 (PipeWire 1.0 and newer)**: create `~/.config/wireplumber/wireplumber.conf.d/51-bluez-no-suspend.conf`:

```
monitor.bluez.rules = [
  {
    matches = [ { node.name = "~bluez_output.*" } ]
    actions = { update-props = { session.suspend-timeout-seconds = 0 } }
  }
]
```

**WirePlumber 0.4**: create `~/.config/wireplumber/bluetooth.lua.d/51-bluez-no-suspend.lua`:

```lua
table.insert(bluez_monitor.rules, {
  matches = { { { "node.name", "matches", "bluez_output.*" } } },
  apply_properties = { ["session.suspend-timeout-seconds"] = 0 },
})
```

Restart WirePlumber (`systemctl --user restart wireplumber`) and reconnect the speaker.

**PulseAudio**: unload the module for the running server with `pactl unload-module module-suspend-on-idle`, and remove the `load-module module-suspend-on-idle` line from `/etc/pulse/default.pa` (or your `~/.config/pulse/default.pa`) to keep it off after a restart. Home Assistant OS does not load this module, so nothing is needed there.

## Air sensor (experimental)

The air sensor measures when each speaker's audio actually leaves the host over Bluetooth, and how far that is from its schedule. The bridge reads a copy of the Bluetooth traffic from the kernel's HCI monitor, takes the loudness pattern of the SBC frames sent to the speaker, and matches it against the loudness pattern of the audio it wrote, once a second over 3-second windows. In `measure` mode playback is not changed: the sensor only measures and reports. In `loop` mode it also corrects the speaker (see Air correction in `loop` below).

**Turning it on.** **Settings → Audio → Air sensor (SBC speakers)** (`EXPERIMENTAL_AIR_SENSOR`, an advanced setting, off by default). The switch takes effect after a **bridge restart**. It works for a speaker only when:

- **Playout sync** is set to **Measure only** (`measure`) or **Measure and correct** (`loop`). With `off` the panel says the sensor needs measure or loop mode.
- The speaker plays **SBC**. For a speaker that also offers AAC, turn on **Prefer SBC codec**; any other codec shows *Unsupported codec*.
- The source is 44.1 or 48 kHz at 16 to 32 bit; anything else shows *Unsupported input*.
- The bridge can open the kernel's HCI monitor, which needs `CAP_NET_RAW` (Linux only). The Home Assistant add-on already has it. Docker needs `NET_RAW` in `cap_add`, as in the documented compose file (see [Docker](/installation/docker/)). LXC and systemd installs run the bridge as root and have it unless the container drops that capability. Without it the panel shows *Unavailable* with "needs CAP_NET_RAW".

**CPU.** On the Home Assistant OS test VM (x86, 2 vCPU) the bridge's Bluetooth monitor reader used 5.0–5.5 % of one core with two streaming links (one speaker playing music, one on a keep-alive stream), about **2.6 % per streaming speaker**. The per-speaker correlation in the speaker's own process was not measured separately. If the sensor ever uses too much processor time, a per-speaker guard stops it and the panel says so. The cost on a Raspberry Pi has not been measured yet.

**How far to trust it.** On the test stand the live air times matched an independent offline analysis of the same Bluetooth capture within 0.04 ms (p95). That check compares window against window, so it does not cover an offset common to a whole stream; the microphone measurements cover the absolute side (see Calibration below).

**The Timing panel.** With the switch on, each speaker's **Timing** panel has an **Air sensor** block, and the device card shows a chip such as `air: Locked, H -32.1 ms`.

| Row | Meaning |
|---|---|
| **State** | One of the states below; when the sensor is off for this speaker or unavailable, the reason is shown |
| **Host placement** | `H`: how far before (negative) or after its schedule the audio left over the air, the median of the last five confirmed windows. It stops at the air: the speaker's own Bluetooth latency is not in it. On the test stand `H` was −31 to −45 ms per stream start, with σ ≈ 0.2 ms within a stream (range ≤ 0.9 ms per start) |
| **Air error** | With a calibration: the speaker's lateness at the sound, `H` plus its measured Bluetooth latency minus the delay set for it. Without one the row says **Needs calibration** |
| **Confidence** | 0 to 1, from the peak ratio and the share of confirmed windows; it falls while the sensor is holding |
| **Peak ratio (PSR)** | How clearly the last confirmed window matched; a window needs at least 1.17, music usually gives 2 to 4 |
| **Last confirmed** | Seconds since the last confirmed window |

| State | When |
|---|---|
| **Off** | The switch is on, but not for this speaker (Playout sync not `measure`, not a Bluetooth speaker) |
| **Unavailable** | No HCI monitor (no `CAP_NET_RAW`, not Linux), the monitor failed, or the CPU guard stopped the sensor |
| **Unsupported codec** / **Unsupported input** | The speaker does not play SBC / the source format is not supported |
| **No signal** | No audio is going to the speaker over the air |
| **Acquiring** | Audio flows; the sensor is searching for the first match after a stream start |
| **Locked** | A window was confirmed within the last 5 s |
| **Holding** | The last confirmed window was 5–20 s ago; the last values stay |
| **Lock lost** | 20 s without a confirmed window (a long quiet passage can cause it); the values are cleared until the next match |

**Calibration.** `H` ends at the air; the time a speaker takes from receiving the audio to playing it (air → sound) differs from speaker to speaker. The group sync wizard measures it with the phone: see [Group sync tuning › Speaker latency (air sensor)](/group-sync-tuning/#speaker-latency-air-sensor). The **reference** you pick must play on the Sendspin schedule: a Sendspin web player on a headset or a wired output, or a bridge speaker with Playout sync set to `measure` and no delay of its own. A reference with Playout sync off (the older output path) or with its own added delay makes the value wrong by that difference. On the test stand the same speaker calibrated to 209 ms against an older-path reference and to 228.7 ms against an on-schedule one; with 209 ms its air error was 13–16 ms off what a microphone heard, with 228.7 ms within 3 ms.

A calibration is stored per speaker, codec, sampling rate and Bluetooth adapter, and listed under **Calibrations** in the Timing panel. **Forget** deletes it: the speaker is then reported uncalibrated for that codec, rate and adapter until the wizard measures it again.

## Air correction in `loop`

With Playout sync in `loop` and the air sensor on, the bridge also uses the air placement to correct the speaker. It is **one correction per stream start**: once the sensor locks (about 6 to 7 s after the output opens) the bridge moves the audio to where the speaker's sound should be, and then only follows slow changes. It is not a continuous chase of the measured value.

Where the audio is placed depends on what the bridge knows about the speaker:

- **Calibrated.** The group sync wizard measured and saved the speaker's Bluetooth latency (see Calibration above). Each start is placed on the schedule, using that latency.
- **Learned.** There is no calibration, so the bridge learns where the speaker played on average in its previous starts. It learns from the first 2 starts, uses the value from then on, and converges in about 6 to 8 starts; the first starts are uncorrected or only roughly placed. A calibration always wins over a learned value.

**Forget** in the Timing panel clears both a calibration and a learned latency for that codec, rate and adapter.

**The delay still moves the sound exactly.** The speaker's delay setting (slider or wizard) works as a trim relative to the delay the calibration or learning was made at: changing it by 30 ms moves the sound by 30 ms, and the correction does not undo it. A delay change does not produce a jump in the air value; the panel shows the trim instead.

**In the Timing panel**, a `loop` speaker with the sensor shows these additional lines:

| Field | Meaning |
|---|---|
| **Air correction** | `calibrated (N ms)`, `learned (N starts)`, `no data (learning: N of 2 starts)` or `inactive: not locked` |
| **Air bias** | The correction currently in force, ms |
| **Air corrections** | How many corrections were made in this stream |
| **Delay trim** | The delay set for the speaker relative to the one the calibration or learning refers to, shown as the trim −(D_out − D_ref), the same sign as in the log, with the resulting target ("target N ms early" when the delay was raised, "late" when lowered), ms |
| **Learned latencies** | The learned entries per codec, rate and adapter, each with **Forget** |

**Guidance.** If a speaker is in `loop` but has no air correction, Diagnostics lists an info item and the speaker works as in `measure`. The reasons are: the air sensor switch is off, the sensor is not running for this speaker (not a Bluetooth speaker), the sensor cannot run (see its state), or it has not locked since the stream started.

**Requirements** are the same as for the sensor: the **Air sensor (SBC speakers)** switch (restart the bridge after changing it), an SBC link at 44.1 or 48 kHz, and `CAP_NET_RAW`. Without any of them `loop` keeps working as before, without the air correction.
