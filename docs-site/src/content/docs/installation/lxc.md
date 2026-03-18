---
title: Installation — LXC (Proxmox & OpenWrt)
description: Install Sendspin Bluetooth Bridge in an LXC container on Proxmox VE or OpenWrt using the host Bluetooth stack
---

import { Aside, Steps } from '@astrojs/starlight/components';

## Why LXC?

LXC is the native non-Docker option for appliance-style hosts such as Proxmox VE and OpenWrt. The bridge runs inside the container, while the **host Bluetooth stack is exposed over D-Bus** and PulseAudio runs inside the container.

| Platform | Bluetooth | Audio | Install path |
|---|---|---|---|
| **Proxmox VE** | Host `bluetoothd` via D-Bus bridge | PulseAudio inside the container | `proxmox-create.sh` |
| **OpenWrt / TurrisOS** | Host `bluetoothd` via D-Bus bridge | PulseAudio inside the container | `openwrt/create.sh` |

## Proxmox VE

### Quick install

On the Proxmox host:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/proxmox-create.sh)
```

### Manual path

<Steps>

1. Create a **privileged Ubuntu 24.04** LXC container.
2. Run the in-container installer:

   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh)
   ```

3. Append the required D-Bus / device rules to `/etc/pve/lxc/<CTID>.conf` on the Proxmox host.
4. Restart the container.

</Steps>

The detailed Proxmox walkthrough remains in [`lxc/README.md`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/README.md).

## OpenWrt / TurrisOS

### Quick install

On the OpenWrt host:

```sh
wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh | sh
```

The full OpenWrt-specific guide remains in [`lxc/openwrt/README.md`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/openwrt/README.md).

## Pairing a speaker

Pair from inside the container with `btctl` (a wrapper that talks to the host Bluetooth daemon through the D-Bus bridge):

```bash
btctl
power on
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
exit
```

## Port planning in LXC deployments

After the first boot, configure ports in `/config/config.json` (or in the web UI, then restart the service):

```json
{
  "WEB_PORT": 8080,
  "BASE_LISTEN_PORT": 8928,
  "BLUETOOTH_DEVICES": [
    {
      "mac": "AA:BB:CC:DD:EE:FF",
      "player_name": "Living Room Speaker",
      "listen_port": 8935,
      "listen_host": "192.168.1.50"
    }
  ]
}
```

- **`WEB_PORT`** controls the direct web UI/API listener for the container.
- **`BASE_LISTEN_PORT`** sets the default Sendspin listener block for devices without explicit `listen_port`.
- **`listen_port`** overrides the player port for one device.
- **`listen_host`** changes the advertised host/IP for the player; it does not change the bind address.

## Multiple LXC bridges on one host

If you run multiple bridge containers on one Proxmox or OpenWrt host:

- give each container a unique `WEB_PORT`
- give each container a unique `BASE_LISTEN_PORT`
- keep each Bluetooth speaker assigned to only one running bridge

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
  In LXC mode the container intentionally uses the host Bluetooth daemon over D-Bus. Do not try to enable a separate `bluetoothd` inside the container.
</Aside>
