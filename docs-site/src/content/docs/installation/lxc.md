---
title: Installation — Proxmox LXC
description: Installing Sendspin Bluetooth Bridge in a Proxmox LXC container
---

import { Steps, Aside } from '@astrojs/starlight/components';

## Why LXC over Docker?

Unlike Docker, an LXC container has its **own bluetoothd and PulseAudio**, providing more stable Bluetooth operation: pairing persists across reboots, no conflicts with the host's bluetoothd.

## Requirements

- Proxmox VE 7.x or 8.x
- USB Bluetooth adapter (recommended: one adapter per speaker)

## Installation

<Steps>

1. **Run the install script**

   On the Proxmox host:

   ```bash
   bash -c "$(wget -qO - https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh)"
   ```

   The script creates an LXC container, installs all dependencies, configures PulseAudio, and starts the service.

2. **Enter the container console**

   ```bash
   pct enter <ID>
   ```

3. **Open the web interface**

   ```
   http://<container-IP>:8080
   ```

4. **Add a Bluetooth device**

   In the web interface go to **Configuration → Bluetooth Devices**, click **Scan** to discover devices or **+ Add Device** to enter a MAC address manually.

</Steps>

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
cd /opt/sendspin-bt-bridge
git pull
systemctl restart sendspin-client
```

<Aside type="tip">
  For multiple speakers, consider creating a separate LXC container per USB Bluetooth adapter. This isolates bluetoothd and PulseAudio, eliminating codec conflicts.
</Aside>
