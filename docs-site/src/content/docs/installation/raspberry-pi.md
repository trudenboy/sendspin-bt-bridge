---
title: Installation — Raspberry Pi
description: Running Sendspin Bluetooth Bridge on Raspberry Pi with Docker
---

import { Aside, Steps } from '@astrojs/starlight/components';

## Supported Models

| Model | Architecture | Docker platform | Status |
|-------|-------------|----------------|--------|
| **Raspberry Pi 5** | aarch64 | `linux/arm64` | ✅ Recommended |
| **Raspberry Pi 4** (2/4/8 GB) | aarch64 | `linux/arm64` | ✅ Recommended |
| **Raspberry Pi 3 Model B+** | armv7l | `linux/arm/v7` | ⚠️ 1–2 speakers max |
| **Raspberry Pi Zero 2 W** | aarch64 | `linux/arm64` | ⚠️ Limited RAM (512 MB) |

<Aside type="tip">
  Use **64-bit Raspberry Pi OS** (aarch64) when possible — it provides better performance and full compatibility.
  32-bit OS (armv7) works but may have resource constraints with multiple speakers.
</Aside>

## Prerequisites

<Steps>

1. **Raspberry Pi OS** (Bookworm or later) installed and updated

2. **Docker Engine** installed:

   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   # Log out and back in for group change to take effect
   ```

3. **Bluetooth adapter** — built-in or USB (CSR8510, TP-Link UB500, etc.)

4. **Audio system** — PipeWire (default on Bookworm) or PulseAudio:

   ```bash
   # Check which audio system is running
   pactl info | grep "Server Name"
   # Expected: "PulseAudio (on PipeWire ...)" or "pulseaudio"
   ```

5. **Speaker paired on the host** (not inside Docker):

   ```bash
   bluetoothctl
   scan on
   # Wait for your speaker to appear, then:
   pair AA:BB:CC:DD:EE:FF
   trust AA:BB:CC:DD:EE:FF
   connect AA:BB:CC:DD:EE:FF
   exit
   ```

</Steps>

## Pre-flight Check

Run the diagnostic script **before** starting the container:

```bash
curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-check.sh | bash
```

This checks Docker, Bluetooth, audio, UID, and memory — and outputs recommended `.env` values.

Example output:
```
═══════════════════════════════════════════════════════
  Sendspin Bluetooth Bridge — Pre-flight Check
═══════════════════════════════════════════════════════

1. Platform
  ✅ Architecture: aarch64 (arm64) — fully supported
  ℹ  Hardware: Raspberry Pi 4 Model B Rev 1.4

2. Memory
  ✅ RAM: 3794 MB — sufficient for multiple speakers

3. Docker
  ✅ Docker installed: Docker version 27.5.1
  ✅ Docker daemon is running
  ✅ Docker Compose available

4. Bluetooth
  ✅ bluetoothd service is running
  ✅ BT controller found: Controller 00:1A:7D:DA:71:13
  ✅ Paired devices: 1
  ℹ    AA:BB:CC:DD:EE:FF — JBL Flip 6

5. Audio System
  ✅ PulseAudio API available (via PipeWire)
  ✅ PulseAudio socket found: /run/user/1000/pulse/native

6. User & UID
  ✅ Current user: pi (UID: 1000)
  ✅ UID is 1000 (default, no .env override needed)

7. D-Bus
  ✅ D-Bus system socket found

═══════════════════════════════════════════════════════
  Summary: 10 passed, 0 warnings, 0 failed
═══════════════════════════════════════════════════════

Recommended .env file:

  BLUETOOTH_MAC=AA:BB:CC:DD:EE:FF
  AUDIO_UID=1000
  TZ=Europe/London
```

## Installation

<Steps>

1. **Create a project directory**

   ```bash
   mkdir ~/sendspin && cd ~/sendspin
   ```

2. **Save the `.env` file** from the pre-flight check output:

   ```env
   BLUETOOTH_MAC=AA:BB:CC:DD:EE:FF
   AUDIO_UID=1000
   TZ=Europe/London
   ```

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
         - ./config:/config
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

5. **Check startup diagnostics**

   ```bash
   docker logs sendspin-client
   ```

   You should see the diagnostics table showing all checks passed:
   ```
   ╔══════════════════════════════════════════════════════╗
   ║  Sendspin Bridge v2.16.1 Diagnostics
   ╠══════════════════════════════════════════════════════╣
   ║  Platform:    aarch64 (arm64)
   ║  Audio:       ✓ PulseAudio (...)
   ║  Bluetooth:   ✓ 00:1A:7D:DA:71:13
   ║  D-Bus:       ✓ host socket mounted
   ╚══════════════════════════════════════════════════════╝
   ```

6. **Open the web interface**

   ```
   http://<raspberry-pi-ip>:8080
   ```

</Steps>

## Troubleshooting

### No sound (silence)

1. Check that the speaker is connected: `bluetoothctl info AA:BB:CC:DD:EE:FF | grep Connected`
2. Check audio sink: `pactl list short sinks | grep bluez`
3. Check mute: `pactl get-sink-mute <sink_index>`
4. Check container logs: `docker logs sendspin-client | grep -E "Audio worker|daemon stderr"`

### UID mismatch

If your user UID is not 1000 (check with `id -u`), set `AUDIO_UID` in `.env`:

```env
AUDIO_UID=1001  # Your actual UID
```

### PipeWire vs PulseAudio

Raspberry Pi OS Bookworm uses PipeWire by default with PulseAudio compatibility. The bridge works with both. To check:

```bash
# Which system is running?
pactl info | grep "Server Name"

# PipeWire output: "PulseAudio (on PipeWire 1.x.x)"
# PulseAudio output: "pulseaudio"
```

### Resource limits

| Model | RAM | Recommended speakers |
|-------|-----|---------------------|
| RPi 5 (4/8 GB) | 4–8 GB | 3–4+ |
| RPi 4 (2 GB) | 2 GB | 2–3 |
| RPi 4 (1 GB) | 1 GB | 1–2 |
| RPi 3 (1 GB) | 1 GB | 1 |
| RPi Zero 2 W | 512 MB | 1 |

### `network_mode: host` explained

The container uses `network_mode: host` because it needs:
- **mDNS** for auto-discovering Music Assistant server on the local network
- **D-Bus** access to the host's `bluetoothd` daemon for Bluetooth control

This means the container shares the host's network stack — the web UI is accessible at `http://<pi-ip>:8080` directly.
