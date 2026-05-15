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

## How to interpret these tiers

- **Confirmed working** doesn't mean "works on every host." A speaker that's clean on PulseAudio 17 + BlueZ 5.82 might still hit a `pulse_latency_msec` tuning step on PipeWire-pulse, or need an adapter swap if the on-board controller is overloaded. Cross-check against [Bluetooth Adapters](/bluetooth-adapters/) and [Troubleshooting](/troubleshooting/) when in doubt.
- **Working with caveats** entries mean the workaround is **available in the bridge or in the docs** — you don't need a custom firmware build to get there.
- **Documented quirks** is here so a future operator can recognize the fingerprint quickly instead of debugging from scratch. If you find a workaround, please report it back so we can re-tier the speaker.
