---
title: Bluetooth Adapters
description: Recommended USB Bluetooth adapters for multi-speaker audio streaming with Sendspin BT Bridge on HAOS, Docker, and LXC
---

## Why the adapter matters

The bridge streams A2DP audio to every configured speaker simultaneously.
Each SBC stream consumes ~345 kbps of Bluetooth bandwidth, so adapter
choice directly affects connection stability, range, and the number of
speakers you can drive from a single controller.

### Key selection criteria

| Criterion | Why it matters |
|---|---|
| **Bluetooth 5.0+** | 4× LE range, better coexistence when juggling multiple connections |
| **Chipset with native btusb support** | Plug-and-play on HAOS without sideloading drivers |
| **Firmware shipped in linux-firmware** | HAOS is immutable — you cannot install extra packages |
| **USB 2.0 nano form factor** | Clean Proxmox USB passthrough, doesn't block adjacent ports |
| **A2DP + SBC** | Mandatory for audio streaming |
| **Stable reconnect behavior** | Headless system with no UI for manual recovery |

## How many adapters do I need?

One Bluetooth adapter supports up to 7 active ACL links, but A2DP
streaming is bandwidth-intensive. For reliable operation:

| Speakers | Recommended adapters |
|---|---|
| 1–3 | 1 adapter |
| 4–5 | 2 adapters (2–3 speakers each) |
| 6+ | 3+ adapters, one per 2–3 speakers |

:::tip[Production reference]
The project's own HAOS test stand runs 2× CSR8510 A10 adapters with
6 configured speakers (3 per adapter) and is migrating to RTL8761B-based
dongles for BT 5.0 range and stability improvements.
:::

## Recommended adapters

All adapters below use the **Realtek RTL8761B** chipset — the de facto
standard for BT 5.0 USB dongles on Linux. The `btusb` driver recognizes
them from kernel 5.8+, and the required firmware (`rtl_bt/rtl8761bu_fw.bin`)
is bundled with `linux-firmware` since 2020.

### 1. TP-Link UB500 (v1 / v2) — Best overall

| Spec | Value |
|---|---|
| Chipset | Realtek RTL8761B |
| Bluetooth | 5.0 (BR/EDR + LE) |
| Linux driver | btusb (kernel ≥ 5.8) |
| USB ID | `2357:0604` |
| Range | ~20 m (Class 1.5) |
| Price | ~$12–15 |

The most widely tested BT 5.0 nano dongle on Linux. Firmware is included
in every modern linux-firmware release, so HAOS picks it up immediately
after USB passthrough.

:::caution[Version warning]
Buy **v1** or **v2** specifically. The **v3** revision carries a BT 5.4
chipset with unverified HAOS compatibility.
:::

### 2. ASUS USB-BT500 — Proven alternative

| Spec | Value |
|---|---|
| Chipset | Realtek RTL8761B |
| Bluetooth | 5.0 (BR/EDR + LE) |
| Linux driver | btusb (kernel ≥ 5.14 by USB ID) |
| USB ID | `0b05:190e` |
| Range | ~10 m (Classic / A2DP) |
| Price | ~$15–20 |

Same RTL8761B chipset in a slightly better-shielded ASUS package. Over
890 reports on linux-hardware.org and well-documented in the Home
Assistant community.

### 3. Plugable USB-BT5

| Spec | Value |
|---|---|
| Chipset | Realtek RTL8761B |
| Bluetooth | 5.0 (BR/EDR + LE) |
| Range | ~40 m (LE), ~10 m (Classic) |
| Price | ~$19 |

Comes with a 2-year warranty and lifetime technical support. The product
page says "incompatible with Linux", but the underlying RTL8761B chipset
works perfectly through `btusb`.

### 4. EDUP EP-B3536 — BT 5.1 option

| Spec | Value |
|---|---|
| Chipset | Realtek RTL8761BUV |
| Bluetooth | 5.1 |
| Price | ~$10–12 |

An evolution of the RTL8761B with BT 5.1 direction finding (not critical
for A2DP but a nice-to-have). Compatible `btusb` driver; may require a
slightly newer linux-firmware for the firmware blob.

### 5. Zexmte / MPOW BT 5.0 Nano — Budget pick

| Spec | Value |
|---|---|
| Chipset | Realtek RTL8761B (nominal) |
| Bluetooth | 5.0 |
| Price | ~$8–10 |

Cheapest RTL8761B option. Good if you need to buy several adapters at
once. Verify the USB ID after receiving — some batches may ship a
different chipset.

## Summary table

| # | Model | Chipset | BT | Linux kernel | Price | Rating |
|---|---|---|---|---|---|---|
| 1 | TP-Link UB500 v1/v2 | RTL8761B | 5.0 | ≥ 5.8 | ~$12 | ⭐⭐⭐⭐⭐ |
| 2 | ASUS USB-BT500 | RTL8761B | 5.0 | ≥ 5.14 | ~$17 | ⭐⭐⭐⭐⭐ |
| 3 | Plugable USB-BT5 | RTL8761B | 5.0 | ≥ 5.8 | ~$19 | ⭐⭐⭐⭐ |
| 4 | EDUP EP-B3536 | RTL8761BUV | 5.1 | ≥ 5.8 | ~$11 | ⭐⭐⭐⭐ |
| 5 | Zexmte BT 5.0 | RTL8761B | 5.0 | ≥ 5.8 | ~$9 | ⭐⭐⭐ |

## Built-in adapters (Raspberry Pi)

Raspberry Pi boards include an on-board Broadcom Bluetooth controller.
It works for basic testing but has significant limitations for
multi-speaker audio streaming:

| Board | Chipset | BT version | Max A2DP streams | Notes |
|---|---|---|---|---|
| **Pi 4 Model B** | BCM4345C0 (CYW43455) | 5.0 (BLE) | **1** | Shared antenna with WiFi, limited A2DP bandwidth |
| **Pi 5** | CYW43455 variant | 5.0 (BLE) | **1** | Same chipset lineage, same single-stream limit |
| **Pi 3 Model B+** | BCM43438 | 4.2 | **1** | Older BT version, lower throughput |

:::caution[Single-stream limitation]
The built-in Raspberry Pi Bluetooth adapter supports only **one
concurrent A2DP audio stream**. To drive multiple speakers you **must**
add one or more USB Bluetooth dongles (see recommended adapters above).
:::

:::caution[2.4 GHz coexistence with onboard WiFi]
On Pi 4 / Pi 5, the onboard WiFi (BCM43455) and any USB BT dongle share
the 2.4 GHz ISM band. When the host is connected to a 2.4 GHz WiFi
network, contention can produce climbing `Tx excessive retries`,
BlueZ stalls, audio dropouts and frozen D-Bus clients (e.g. `btop`).
If the router supports it, prefer 5 GHz on the host. See
[Audio stuttering and D-Bus freezes on Raspberry Pi](/sendspin-bt-bridge/troubleshooting/#audio-stuttering-and-d-bus-freezes-on-raspberry-pi-with-a-usb-bt-dongle)
in the Troubleshooting page for the diagnostic and the `nmcli` fix.
:::

:::tip[rfkill on Raspberry Pi]
On some Pi OS installations the on-board Bluetooth is soft-blocked by
default. The bridge automatically runs `rfkill unblock bluetooth` at
startup, but if you run without Docker you may need to execute it
manually:
```bash
sudo rfkill unblock bluetooth
```
:::

## Software workarounds for adapter / BlueZ regressions

The bridge ships **advanced compatibility tools** for specific kernel, BlueZ, or PulseAudio failures. They live in **Configuration → Bluetooth → Advanced recovery workarounds** and stay hidden until you turn on **Advanced compatibility tools** on the General tab. The UI disables tools unsupported by the running host and explains why. See the [Web UI page](/sendspin-bt-bridge/web-ui/#advanced-bluetooth-compatibility-tools) for details.

| Symptom | Toggle |
|---|---|
| Speaker connects but PulseAudio reports no sink (BlueZ 5.86 dual-role regression, [bluez/bluez#1922](https://github.com/bluez/bluez/issues/1922)) | `EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE` |
| Sink appears intermittently or only after a manual replug | `EXPERIMENTAL_PA_MODULE_RELOAD` |
| The whole adapter goes silent under load and bridge cannot recover it | `EXPERIMENTAL_ADAPTER_AUTO_RECOVERY` (rfkill check → MGMT/HCI power-cycle → optional USB reset) |
| Cannot pair a second speaker on a single-adapter setup while the first one is streaming | **Temporarily disconnect other speakers** in the Scan modal |
| Speaker shows no SSP confirmation at all and bridge waits indefinitely | **NoInputNoOutput pair agent** in the Scan modal |
| A multi-profile device refuses to finish pairing unless HFP/HSP is accepted | **Authorize HFP/HSP for this pair** for that attempt; playback is still A2DP-only |

Treat these as targeted painkillers. Do not turn them on preemptively. Pairing compatibility options are one-shot, reset after every request, and the agent rejects requests from every MAC except the selected target.

## Adapters to avoid

| Adapter / chipset | Problem |
|---|---|
| **ZEXMTE BT 5.3 Long Range** (180 m / high-gain antenna, ASIN `B0CP5WQ7L8`) | Detects, pairs, plays — but unstable for A2DP music streaming (community report, [#295](https://github.com/trudenboy/sendspin-bt-bridge/issues/295#issuecomment-4441986718)) |
| **TP-Link UB500 Plus** (BT 5.3 with adjustable external antenna, ASIN `B0DHJHMHFS`) | Same — detects and pairs, A2DP unstable. Avoid in favour of the regular UB500 nano. Community report, [#295](https://github.com/trudenboy/sendspin-bt-bridge/issues/295#issuecomment-4441986718) |
| **CSR8510 A10** | BT 4.0, limited range (~10 m), aging silicon |
| **Broadcom BCM20702** | BT 4.0, firmware-loading issues on immutable systems |
| **Qualcomm QCA61x4** | Needs proprietary firmware, unstable with bluez |
| **TP-Link UB500 v3** | BT 5.4 with a different chipset — HAOS compatibility unconfirmed |
| **Any WiFi + BT combo** | Conflicts with existing WiFi, complex USB passthrough |
| **BT 5.2+ LE Audio dongles** | LC3 codec is not yet supported by PulseAudio 17 |
| **aptX / Snapdragon Sound transmitters** (e.g. Creative BT-W6, `B0DG34HRNC`) | Proprietary stack; the bridge streams strictly via A2DP/SBC |

:::tip[Pattern across community reports]
**Long-range / high-gain-antenna variants of BT 5.3+ dongles look attractive on paper but consistently underperform for A2DP streaming.** The combination of newer Realtek silicon, larger antenna, and BlueZ ≥ 5.78 firmware quirks is currently more fragile than the boring `RTL8761B` BT 5.0 nano stick. If you need more range, prefer an **active USB extension cable** (5–10 m with powered repeater) over a long-range dongle.
:::

## Community-tested adapters (Amazon ASINs)

Community datapoints from [#295](https://github.com/trudenboy/sendspin-bt-bridge/issues/295#issuecomment-4441986718) (sirs2k, tested on HAOS, hci0). All four detect and pair correctly; A2DP music-streaming behaviour is the differentiator:

| Verdict | Product | ASIN | BT | Notes |
|---|---|---|---|---|
| ⭐⭐⭐⭐⭐ Best | UGREEN 80889 USB Bluetooth 5.0 Adapter | [`B08R8992YC`](https://www.amazon.com.au/dp/B08R8992YC) | 5.0 | Realtek RTL8761B nano, model 80889 |
| ⭐⭐⭐⭐ Good | TP-Link UB500 Nano (UK Version) | [`B09C25VRXD`](https://www.amazon.com.au/dp/B09C25VRXD) | 5.0 | Same v1/v2 hardware as the global TP-Link UB500 |
| ❌ Avoid | TP-Link UB500 **Plus** (BT 5.3 + external antenna) | [`B0DHJHMHFS`](https://www.amazon.com.au/dp/B0DHJHMHFS) | 5.3 | Pairs and plays, A2DP unstable for music |
| ❌ Avoid | ZEXMTE BT 5.3 Long Range (180 m antenna) | [`B0CP5WQ7L8`](https://www.amazon.com.au/dp/B0CP5WQ7L8) | 5.3 | Pairs and plays, A2DP unstable for music |

If you have a datapoint to add — model, ASIN, BlueZ version, and whether it sustained A2DP audio under load — please [open an issue](https://github.com/trudenboy/sendspin-bt-bridge/issues/new) so future readers benefit.

## Migration from CSR8510 to RTL8761B

If you are upgrading from the older CSR8510 A10 adapters:

1. Purchase 2× TP-Link UB500 v1/v2 (or any RTL8761B dongle above).
2. **Proxmox**: update USB device mappings to the new VID:PID.
3. **HAOS**: the adapters are recognized automatically (`btusb` + `linux-firmware`).
4. Verify with `bluetoothctl list` — you should see two controllers.
5. Update adapter MAC addresses in the bridge configuration (hci0 / hci1).
6. Re-pair each speaker and test A2DP playback.
7. Monitor reconnect stability over 24 hours before considering the migration complete.

## Proxmox USB passthrough layout

A typical two-adapter setup for 4–5 speakers:

```
Proxmox Host
├── USB Mapping "Audio"  → TP-Link UB500 #1 (hci0) → 2–3 speakers
├── USB Mapping "BT2"    → TP-Link UB500 #2 (hci1) → 2 speakers
└── HAOS VM
    └── Sendspin BT Bridge
        ├── BluetoothManager (hci0)
        └── BluetoothManager (hci1)
```

See [Devices & Adapters](/sendspin-bt-bridge/devices/) for adapter naming,
binding speakers to specific controllers, and managing the device fleet.
