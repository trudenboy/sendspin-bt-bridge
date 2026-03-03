---
title: Devices & Adapters
description: Managing Bluetooth devices and adapters in Sendspin Bluetooth Bridge
---

import { Aside } from '@astrojs/starlight/components';

## Adding a Device

### Via web interface (recommended)

1. Open **⚙️ Configuration → Bluetooth Devices**
2. Click **🔍 Scan** — the bridge will scan for ~10 seconds
3. Click a discovered device to add it to the table
4. Fill in the player name
5. Click **Save & Restart**

### Manually

Click **+ Add Device** and fill in:
- **Player Name** — name that will appear in Music Assistant
- **MAC Address** — in format `AA:BB:CC:DD:EE:FF`
- **Adapter** — optional, for multi-adapter setups
- **Listen Address / Port** — if non-default values are needed
- **Delay ms** — A2DP latency compensation

## Pairing

If the device hasn't been paired with the host yet:

1. Put the speaker in pairing mode
2. Click **🔗 Re-pair** in the device card
3. Wait ~25 seconds (pair + trust + connect)

## Bluetooth Adapters

The bridge auto-detects all adapters via `bluetoothctl list`. The **↺ Refresh** button updates the list.

### Binding a device to an adapter

In the **Adapter** field specify:
- `hci0`, `hci1` — by interface name
- `AA:BB:CC:DD:EE:FF` — by adapter MAC (recommended in LXC where `hciN` may be unavailable)

### Multi-adapter configuration

```json
{
  "BLUETOOTH_DEVICES": [
    { "mac": "AA:BB:CC:DD:EE:FF", "player_name": "Living Room", "adapter": "hci0" },
    { "mac": "11:22:33:44:55:66", "player_name": "Kitchen", "adapter": "hci1" }
  ]
}
```

## A2DP Latency Compensation

All Bluetooth devices have built-in buffer latency (typically 100–600 ms). For group playback this causes desynchronization.

The `static_delay_ms` field compensates for this:
- Negative value — delay this player (it's "faster" than others)
- Positive value — speed up data delivery (player is "slower")

Example: if one speaker lags 300 ms behind another, set `static_delay_ms: 300` for the lagging one.

The current delay is shown in the **Sync** section of the device card.

## Release / Reclaim

**🔓 Release** puts the device in "free" mode:
- Bridge stops trying to reconnect
- Bluetooth adapter is freed for another source
- Device remains in config

**🔒 Reclaim** returns management to the bridge.

Useful when you want to temporarily connect a speaker to a phone without stopping the service.
