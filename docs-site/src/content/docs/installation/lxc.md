---
title: Installation — LXC (Proxmox & OpenWrt)
description: Installing Sendspin Bluetooth Bridge in an LXC container on Proxmox VE or OpenWrt
---


## Why LXC over Docker?

Unlike Docker, an LXC container has its **own bluetoothd and PulseAudio** (Proxmox) or uses the host's bluetoothd via D-Bus (OpenWrt), providing more stable Bluetooth operation: pairing persists across reboots, no conflicts with the host's bluetoothd.

## Supported Platforms

| Platform | Script | Status |
|----------|--------|--------|
| **Proxmox VE** 7/8 | [`proxmox-create.sh`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/proxmox-create.sh) | ✅ Stable |
| **OpenWrt** 23.x+ / TurrisOS 9.x | [`openwrt/create.sh`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/openwrt/create.sh) | ✅ Stable |

## Proxmox VE

### Requirements

- Proxmox VE 7.x or 8.x
- USB Bluetooth adapter (recommended: one adapter per speaker)

### Quick Install

On the Proxmox host:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/proxmox-create.sh)
```

The script interactively prompts for container ID, hostname, RAM, disk, network, and USB Bluetooth passthrough.

### Manual Install

<Steps>

1. Create a new **privileged** LXC container (**Ubuntu 24.04**, 512 MB RAM, 4 GB disk)
2. Start the container and open a shell (`pct enter <CTID>`)
3. Run the installer:
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh)
   ```
4. Append to `/etc/pve/lxc/<CTID>.conf` on the **Proxmox host**:
   ```
   lxc.apparmor.profile: unconfined
   lxc.cgroup2.devices.allow: c 166:* rwm
   lxc.cgroup2.devices.allow: c 13:* rwm
   lxc.cgroup2.devices.allow: c 10:232 rwm
   lxc.mount.entry: /run/dbus bt-dbus none bind,create=dir 0 0
   lxc.cgroup2.devices.allow: c 189:* rwm
   ```
5. Restart the container: `pct restart <CTID>`

</Steps>

## OpenWrt / TurrisOS

### Requirements

- OpenWrt 23.x+ or TurrisOS 9.x
- ≥1 GB RAM, ≥2 GB free storage
- USB Bluetooth adapter

### Quick Install

On the OpenWrt host:

```sh
wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh | sh
```

The script installs LXC and Bluetooth packages via `opkg`, creates an Ubuntu 24.04 container, configures D-Bus bridge and cgroup rules, and installs a procd init.d script for autostart.

For full manual install steps and known issues, see [lxc/openwrt/README.md](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/openwrt/README.md).

## Pairing a speaker

If the speaker hasn't been paired yet:

1. Put the speaker in pairing mode
2. Click **🔍 Scan** in the web UI and wait ~10 seconds
3. Click **Re-pair** next to the found device

Or via `bluetoothctl` inside the container:

```bash
bluetoothctl
# power on
# scan on
# pair AA:BB:CC:DD:EE:FF
# trust AA:BB:CC:DD:EE:FF
# connect AA:BB:CC:DD:EE:FF
```

## Service management

```bash
systemctl status sendspin-client
systemctl restart sendspin-client
journalctl -u sendspin-client -f
```

## Updating

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/upgrade.sh)
```

<Aside type="tip">
  For multiple speakers, consider creating a separate LXC container per USB Bluetooth adapter. This isolates bluetoothd and PulseAudio, eliminating codec conflicts.
</Aside>
