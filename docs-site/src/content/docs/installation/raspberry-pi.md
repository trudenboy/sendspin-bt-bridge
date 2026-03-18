---
title: Installation — Raspberry Pi
description: Run Sendspin Bluetooth Bridge on Raspberry Pi with Docker, including current port-planning options
---

import { Aside, Steps } from '@astrojs/starlight/components';

## Supported models

| Model | Architecture | Docker platform | Status |
|---|---|---|---|
| **Raspberry Pi 5** | aarch64 | `linux/arm64` | ✅ Recommended |
| **Raspberry Pi 4** | aarch64 | `linux/arm64` | ✅ Recommended |
| **Raspberry Pi 3 B+** | armv7l | `linux/arm/v7` | ⚠️ Best for 1–2 speakers |
| **Raspberry Pi Zero 2 W** | aarch64 | `linux/arm64` | ⚠️ Limited RAM |

<Aside type="tip">
  Use 64-bit Raspberry Pi OS when possible. It gives the best experience for multiple Bluetooth speakers.
</Aside>

## Quick start

The one-liner installer is the fastest path:

```bash
curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-install.sh | bash
```

It checks the host, installs Docker if needed, writes a working Compose setup, and can help with Bluetooth pairing.

After installation, the web UI is available on `http://<raspberry-pi-ip>:8080` unless you changed `WEB_PORT`.

## Manual installation

<Steps>

1. **Prepare the host**

   - install a current Raspberry Pi OS
   - install Docker
   - pair the Bluetooth speaker on the host with `bluetoothctl`

2. **Run the pre-flight check**

   ```bash
   curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-check.sh | bash
   ```

3. **Create a project directory**

   ```bash
   mkdir -p ~/sendspin-bt-bridge && cd ~/sendspin-bt-bridge
   ```

4. **Create `.env`**

   ```env
   AUDIO_UID=1000
   TZ=Europe/London
   WEB_PORT=8080
   BASE_LISTEN_PORT=8928
   ```

5. **Download the current Compose file**

   ```bash
   curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/docker-compose.yml -o docker-compose.yml
   mkdir -p config
   docker compose up -d
   ```

6. **Open the web UI**

   ```text
   http://<raspberry-pi-ip>:<WEB_PORT>
   ```

</Steps>

## Port planning on Raspberry Pi

- **`WEB_PORT`** changes the direct web UI/API listener on the Pi.
- **`BASE_LISTEN_PORT`** changes the default Sendspin port block for speakers.
- Devices without an explicit `listen_port` use `BASE_LISTEN_PORT + device_index`.
- You can override a single device with `listen_port` and `listen_host` in the web UI or `/config/config.json`.

Example advanced device entry:

```json
{
  "mac": "AA:BB:CC:DD:EE:FF",
  "player_name": "Kitchen Speaker",
  "listen_port": 8935,
  "listen_host": "192.168.1.50"
}
```

`listen_host` changes only the advertised host/IP for the player. It does not change the bind address inside the container.

## Running more than one bridge on a Pi or LAN segment

If you run multiple bridge containers or combine a Raspberry Pi bridge with another bridge on the same host/network namespace:

- give each bridge its own `WEB_PORT`
- give each bridge its own `BASE_LISTEN_PORT`
- do **not** assign the same Bluetooth speaker to two running bridges

## Verify the runtime

```bash
docker logs -f sendspin-client
curl -s http://localhost:${WEB_PORT:-8080}/api/preflight | python3 -m json.tool
```

## Updating

```bash
cd ~/sendspin-bt-bridge
docker compose pull
docker compose up -d
```

## Notes

- `network_mode: host` is required for Bluetooth control and Music Assistant auto-discovery.
- Raspberry Pi OS Bookworm uses PipeWire with PulseAudio compatibility by default; the bridge works with both PipeWire and PulseAudio.
- Changes to devices, adapters, `WEB_PORT`, `BASE_LISTEN_PORT`, and Music Assistant connection settings require a container restart.
