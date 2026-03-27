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

## Adapters to avoid

| Adapter / chipset | Problem |
|---|---|
| **CSR8510 A10** | BT 4.0, limited range (~10 m), aging silicon |
| **Broadcom BCM20702** | BT 4.0, firmware-loading issues on immutable systems |
| **Qualcomm QCA61x4** | Needs proprietary firmware, unstable with bluez |
| **TP-Link UB500 v3** | BT 5.4 with a different chipset — HAOS compatibility unconfirmed |
| **Any WiFi + BT combo** | Conflicts with existing WiFi, complex USB passthrough |
| **BT 5.2+ LE Audio dongles** | LC3 codec is not yet supported by PulseAudio 17 |

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
