---
title: Installation — Home Assistant Addon
description: Step-by-step installation of Sendspin Bluetooth Bridge as a Home Assistant addon
---


## Requirements

- Home Assistant OS or Supervised
- Bluetooth adapter accessible to the HA host
- Music Assistant Server running on your network

## Supported platforms

| Architecture | HA devices | Status |
|---|---|---|
| **amd64** (x86_64) | Intel NUC, Mini PCs, Proxmox/VMware VMs | ✅ Tested |
| **aarch64** (ARM64) | HA Green, HA Yellow, Raspberry Pi 4/5, ODROID N2+ | ✅ Community-tested |
| **armv7** (ARM 32-bit) | Raspberry Pi 3, ODROID XU4, Tinker Board | ⚠️ Best-effort |

## Installation

<Steps>

1. **Add the addon repository**

   Click the button to add the repository automatically:

   [![Add repository to HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

   Or manually: **Settings → Add-ons → Add-on store → ⋮ → Repositories** and add:
   ```
   https://github.com/trudenboy/sendspin-bt-bridge
   ```

2. **Install the addon**

   Find **Sendspin Bluetooth Bridge** in the addon store and click **Install**.

3. **Configure the addon**

   Go to the **Configuration** tab and add your devices:

   ```yaml
   sendspin_server: auto          # or your MA server hostname/IP
   sendspin_port: 9000
   bluetooth_devices:
     - mac: "AA:BB:CC:DD:EE:FF"
       player_name: "Living Room Speaker"
     - mac: "11:22:33:44:55:66"
       player_name: "Kitchen Speaker"
       adapter: hci1              # only needed for multi-adapter setups
       static_delay_ms: -500      # A2DP latency compensation in ms
   ```

4. **Start the addon**

   Click **Start**. The addon will appear in the HA sidebar.

</Steps>

## Opening the web interface

The addon provides a web UI via **HA Ingress** — click **Open Web UI** on the addon page or use the sidebar link. No port forwarding required.

The interface automatically applies the HA theme (dark/light) via the Ingress `postMessage` API.

## Audio routing (HA OS)

The addon requests `audio: true` in its manifest, so the HA Supervisor automatically injects `PULSE_SERVER`. No manual socket configuration needed.

## Applying configuration changes

Configuration changes take effect after a restart. Use the **Restart** button on the addon page or click **Save & Restart** in the web interface.

<Aside type="tip">
  If Music Assistant doesn't see the player after starting — check that the **Sendspin** provider is enabled in MA. Go to Settings → Providers and make sure Sendspin is active.
</Aside>
