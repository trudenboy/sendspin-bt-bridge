---
title: Installation — Docker Compose
description: Running Sendspin Bluetooth Bridge with Docker Compose
---

import { Steps, Aside } from '@astrojs/starlight/components';

## Requirements

- Docker Engine and Docker Compose
- Bluetooth adapter on the host
- PulseAudio or PipeWire on the host
- Music Assistant Server on your network

## Quick Start

<Steps>

1. **Clone the repository or create `docker-compose.yml`**

   ```yaml
   services:
     sendspin-client:
       image: ghcr.io/trudenboy/sendspin-bt-bridge:latest
       container_name: sendspin-client
       network_mode: host
       cap_add:
         - NET_ADMIN
         - NET_RAW
         - SYS_ADMIN
       environment:
         - SENDSPIN_SERVER=auto
         - SENDSPIN_PORT=9000
         - BLUETOOTH_MAC=${BLUETOOTH_MAC:-}
         - TZ=America/New_York
       volumes:
         - /var/run/dbus:/var/run/dbus
         - /run/user/${AUDIO_UID:-1000}/pulse:/run/user/1000/pulse
         - /run/user/${AUDIO_UID:-1000}/pipewire-0:/run/user/1000/pipewire-0
         - /etc/docker/Sendspin:/config
       restart: unless-stopped
   ```

2. **Configure environment variables**

   Create a `.env` file next to `docker-compose.yml`:

   ```env
   BLUETOOTH_MAC=AA:BB:CC:DD:EE:FF
   AUDIO_UID=1000
   ```

3. **Start the container**

   ```bash
   docker compose up -d
   ```

4. **Open the web interface**

   ```
   http://localhost:8080
   ```

</Steps>

## Network requirements

The container uses `network_mode: host`, required for:
- MA server discovery via mDNS (`auto`)
- Bluetooth D-Bus access

## Capabilities

| Capability | Purpose |
|---|---|
| `NET_ADMIN` | Network interface management (BT) |
| `NET_RAW` | Raw socket access for BT |
| `SYS_ADMIN` | D-Bus mount, PulseAudio management |

<Aside type="caution">
  `privileged: true` is **not required** and not recommended. The listed `cap_add` are sufficient.
</Aside>

## Volumes

| Volume | Description |
|---|---|
| `/var/run/dbus` | D-Bus socket for bluetoothctl |
| `/run/user/UID/pulse` | PulseAudio socket |
| `/run/user/UID/pipewire-0` | PipeWire socket |
| `/etc/docker/Sendspin` | Config directory (config.json) |

## View logs

```bash
docker logs -f sendspin-client
```
