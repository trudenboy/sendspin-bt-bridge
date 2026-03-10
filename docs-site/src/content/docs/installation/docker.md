---
title: Installation — Docker Compose
description: Running Sendspin Bluetooth Bridge with Docker Compose
---

import { Aside } from '@astrojs/starlight/components';

## Requirements

- Docker Engine and Docker Compose
- Bluetooth adapter on the host
- PulseAudio or PipeWire on the host
- Music Assistant Server on your network

The Docker image supports `linux/amd64`, `linux/arm64`, and `linux/arm/v7` architectures.

<Aside type="tip">
  Using a **Raspberry Pi**? See the dedicated [Raspberry Pi guide](/installation/raspberry-pi/) for model-specific instructions and a pre-flight check script.
</Aside>

## Pre-flight Check

Before starting, verify your host is ready:

```bash
curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-check.sh | bash
```

This checks Docker, Bluetooth, audio system, UID, and outputs recommended `.env` values. Works on any Linux host, not just Raspberry Pi.

## Quick Start

<Steps>

1. **Pair your Bluetooth speaker on the host first**

   ```bash
   bluetoothctl
   scan on
   pair AA:BB:CC:DD:EE:FF
   trust AA:BB:CC:DD:EE:FF
   connect AA:BB:CC:DD:EE:FF
   exit
   ```

2. **Create `.env`** with your settings:

   ```env
   BLUETOOTH_MAC=AA:BB:CC:DD:EE:FF
   AUDIO_UID=1000
   TZ=America/New_York
   ```

   <Aside type="caution">
     Check your UID with `id -u`. If it's not 1000, set `AUDIO_UID` accordingly — otherwise audio won't work.
   </Aside>

3. **Create `docker-compose.yml`**

   ```yaml
   services:
     sendspin-client:
       image: ghcr.io/trudenboy/sendspin-bt-bridge:latest
       container_name: sendspin-client
       restart: unless-stopped
       network_mode: host
       volumes:
         - /var/run/dbus:/var/run/dbus
         - /run/user/${AUDIO_UID:-1000}/pulse:/run/user/${AUDIO_UID:-1000}/pulse
         - /run/user/${AUDIO_UID:-1000}/pipewire-0:/run/user/${AUDIO_UID:-1000}/pipewire-0
         - /etc/docker/Sendspin:/config
       environment:
         - SENDSPIN_SERVER=auto
         - BLUETOOTH_MAC=${BLUETOOTH_MAC:-}
         - TZ=${TZ:-UTC}
         - WEB_PORT=8080
         - CONFIG_DIR=/config
         - PULSE_SERVER=unix:/run/user/${AUDIO_UID:-1000}/pulse/native
         - XDG_RUNTIME_DIR=/run/user/${AUDIO_UID:-1000}
       devices:
         - /dev/bus/usb:/dev/bus/usb
       cap_add:
         - NET_ADMIN
         - NET_RAW
   ```

4. **Start the container**

   ```bash
   docker compose up -d
   ```

5. **Verify startup**

   ```bash
   docker logs sendspin-client
   ```

   Check the diagnostics table for any ✗ marks.

6. **Open the web interface**

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

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SENDSPIN_SERVER` | `auto` | MA server address; `auto` uses mDNS |
| `BLUETOOTH_MAC` | — | Speaker MAC (can also configure via web UI) |
| `AUDIO_UID` | `1000` | Host user UID for audio socket paths |
| `TZ` | `UTC` | Container timezone |
| `WEB_PORT` | `8080` | Web interface port |
| `PULSE_SERVER` | — | PulseAudio server socket path |

## View logs

```bash
docker logs -f sendspin-client
```

## Verify setup via API

After the container starts, check the preflight endpoint:

```bash
curl -s http://localhost:8080/api/preflight | python3 -m json.tool
```
