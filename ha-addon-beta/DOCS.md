# Sendspin Bluetooth Bridge (Beta)

## About

![Beta channel notice](https://img.shields.io/badge/Beta%20channel-Experimental-ef4444?style=for-the-badge&labelColor=991b1b&color=ef4444)

**Beta channel notice:** This Home Assistant addon variant tracks the `beta` image lane. Install this variant from the store to receive Beta builds; the bridge UI only indicates the installed track, while switching tracks still happens in the Home Assistant store.

The Sendspin Bluetooth Bridge addon connects
[Music Assistant](https://music-assistant.io/) to Bluetooth speakers via the
Sendspin protocol. It streams audio over Bluetooth A2DP, turning any paired
Bluetooth speaker into a fully controllable Music Assistant player—with volume
control, play/pause, and multi-room grouping.

Each speaker runs as an isolated subprocess with its own PulseAudio context,
ensuring correct audio routing even with multiple speakers connected
simultaneously.

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**.
2. Click the **⋮** menu (top-right) and select **Repositories**.
3. Add the repository URL:

   ```text
   https://github.com/trudenboy/sendspin-bt-bridge
   ```

4. Close the dialog—**Sendspin Bluetooth Bridge (Beta)** now appears in the store.
5. Click it, then click **Install**.

## Requirements

| Requirement | Details |
|---|---|
| **HA installation type** | Home Assistant OS or Home Assistant Supervised |
| **Bluetooth adapter** | Built-in or USB dongle visible to the host |
| **Bluetooth speaker(s)** | Paired to the host before starting the addon |
| **Music Assistant** | Version 2.x with the Sendspin provider enabled (MA server ≥ 5.1.3) |

### Supported architectures

| Architecture | Example hardware |
|---|---|
| `aarch64` | Raspberry Pi 4 / 5, Home Assistant Green, Home Assistant Yellow |
| `amd64` | Intel/AMD x86-64 NUCs, Proxmox VMs |
| `armv7` | Raspberry Pi 3 (limited RAM—may struggle with multiple speakers) |

## Quick Start

1. **Pair your Bluetooth speaker** using `bluetoothctl` in the Host terminal
   (Settings → System → Hardware → ⋮ → Terminal) or via the HA Bluetooth
   integration.
2. **Open the addon Configuration tab** and add a device under
   `bluetooth_devices` with the speaker's MAC address and a player name.
3. **Set `sendspin_server`** to the IP of your Music Assistant instance, or
   leave `auto` to discover it via mDNS (recommended).
4. **Start the addon.** Check the Log tab for connection status.
5. The speaker now appears as a player in Music Assistant—stream away!

## Update channels in Home Assistant

Home Assistant packaging now uses the **installed add-on track** as the only real channel selector:

| Concept | What it means | How it changes |
|---|---|---|
| **Installed add-on track** | The actual add-on variant installed from the HA store (`stable`, `RC`, or `Beta`). This determines the add-on slug, branding, default ingress port, default player listen-port base, startup policy, and update lane. | Install or switch the matching add-on variant in the HA store. |
| **Bridge UI indication** | The bridge web UI shows which track is installed and explains how updates work for that track. | Read-only indication; switching tracks still happens in the HA store. |

### Published add-on variants

| Track | Repository directory | Add-on slug | Default ingress | Default player port base | Startup default |
|---|---|---|---|---|---|
| **Stable** | `ha-addon/` | `sendspin_bt_bridge` | `8080` | `8928` | `auto` |
| **RC** | generated `ha-addon-rc/` | `sendspin_bt_bridge_rc` | `8081` | `9028` | `manual` |
| **Beta** | generated `ha-addon-beta/` | `sendspin_bt_bridge_beta` | `8082` | `9128` | `manual` |

Important:

- this checked-in `ha-addon/` directory is the **stable** source surface; prerelease variants are generated into `ha-addon-rc/` and `ha-addon-beta/`
- stable / RC / Beta variants can run side by side on one HAOS host because they use different default HA ingress ports and different default player listen-port ranges
- do **not** configure the same Bluetooth speaker in more than one variant at the same time
- do **not** let multiple variants manage the same Bluetooth adapter unless you intentionally isolate devices and ports
- if you set manual `web_port` or `base_listen_port` overrides, keep them unique across variants

## Configuration

### General options

| Option | Type | Default | Description |
|---|---|---|---|
| `sendspin_server` | string | `auto` | Hostname or IP of the Music Assistant server. `auto` uses mDNS discovery (recommended). |
| `sendspin_port` | port | `9000` | WebSocket port exposed by the MA Sendspin provider. Only change if you run multiple MA instances or use a custom port. |
| `web_port` | port | _(track default)_ | Optional direct host-network web listener. In add-on mode ingress keeps using the installed track default (`8080` stable / `8081` RC / `8082` beta); setting this opens an additional direct listener only when it differs from that fixed ingress port. |
| `base_listen_port` | port | _(track default)_ | Optional starting port for auto-assigned Sendspin player listeners. Defaults are `8928` (stable), `9028` (RC), and `9128` (beta). |
| `bridge_name` | string | _(empty)_ | Custom name for this bridge instance. When empty the system hostname is used automatically. |
| `tz` | string | _(empty)_ | IANA timezone (e.g. `Europe/London`, `America/New_York`). Leave empty to inherit the Home Assistant system timezone. |
| `pulse_latency_msec` | int | `200` | PulseAudio buffer latency in milliseconds. Higher values (400–600) reduce audio dropouts on slow hardware at the cost of slightly higher latency. |
| `prefer_sbc_codec` | bool | `false` | Force the SBC Bluetooth codec after each connection. SBC uses less CPU than AAC/LDAC—useful on low-power hardware with multiple speakers. Enable alongside PCM 44.1 kHz / 16-bit in MA for maximum CPU savings. |
| `bt_check_interval` | int | `10` | Bluetooth reconnect check interval in seconds. Lower values detect disconnects faster. |
| `bt_max_reconnect_fails` | int | `0` | Maximum consecutive reconnect attempts before giving up. `0` means unlimited (keep retrying forever). |
| `auth_enabled` | bool | `false` | Enable password protection for the web UI. Set the password through the web interface after enabling. |
| `log_level` | list | `info` | Logging verbosity: `info` or `debug`. Use `debug` when troubleshooting. |
### Music Assistant API options

| Option | Type | Default | Description |
|---|---|---|---|
| `ma_api_url` | string | _(empty)_ | Music Assistant REST API URL (e.g. `http://192.168.1.100:8095`). Auto-detected in addon mode. |
| `ma_api_token` | string | _(empty)_ | MA API token. **In addon mode** — click "Sign in with Home Assistant" in the web UI to create one automatically (no manual setup needed). For Docker/LXC — create a token in MA → Settings → API Tokens and paste it here. |
| `volume_via_ma` | bool | `true` | Route volume commands through the MA API instead of controlling PulseAudio directly. Keeps MA and the speaker in sync. |

### Bluetooth devices

```yaml
bluetooth_devices:
  - mac: "AA:BB:CC:DD:EE:FF"
    player_name: "Living Room Speaker"
```

Each entry in the `bluetooth_devices` list represents one Bluetooth speaker.

| Field | Required | Type | Description |
|---|---|---|---|
| `mac` | **yes** | string | Bluetooth MAC address of the speaker (`AA:BB:CC:DD:EE:FF`). Find it with `bluetoothctl devices` after pairing. |
| `player_name` | **yes** | string | Display name shown in Music Assistant. Must be unique across all devices. |
| `adapter` | no | string | MAC address of the Bluetooth adapter to use for this device. Leave blank to use the system default adapter. |
| `static_delay_ms` | no | int | Fixed latency offset in milliseconds. Use negative values (typically `-500`) to compensate for Bluetooth A2DP buffering. `0` for no adjustment. |
| `listen_host` | no | string | Override the listen address for this device's Sendspin server. |
| `listen_port` | no | port | Override the listen port for this device's Sendspin server. |
| `enabled` | no | bool | Set to `false` to temporarily disable a device without removing it from the config. |
| `preferred_format` | no | string | Audio format preference string passed to the Sendspin daemon. |
| `keepalive_silence` | no | bool | Send silent audio frames to prevent the Bluetooth speaker from entering standby. |
| `keepalive_interval` | no | int | Interval in seconds between keepalive silence frames. |

### Bluetooth adapters

```yaml
bluetooth_adapters:
  - id: "hci0"
    name: "Built-in Adapter"
```

Optional—leave empty for automatic detection. Only needed when you have
multiple Bluetooth adapters and want to assign specific speakers to specific
adapters.

| Field | Required | Type | Description |
|---|---|---|---|
| `id` | **yes** | string | Adapter identifier (`hci0`, `hci1`, etc.). |
| `mac` | no | string | MAC address of the adapter (for display purposes). |
| `name` | no | string | Friendly name shown in the web UI. |

## Port strategy and addon tracks

### Addon tracks

The Home Assistant addon track is the variant you actually installed (`stable`, `RC`, or `Beta` when published).
That installed variant determines the update lane. The bridge web UI only indicates the current track and the
appropriate update instructions.

### Default ports by addon track

When multiple addon variants are installed on the same HAOS host, they use different default ports to reduce collisions:

| Track | Default ingress / web port | Default base listen port |
|---|---|---|
| Stable | `8080` | `8928` |
| RC | `8081` | `9028` |
| Beta | `8082` | `9128` |

### Manual port overrides

- Leave `web_port` empty if you only use the addon through HA Ingress.
- Set `web_port` only when you want an additional direct host-network listener.
- Leave `base_listen_port` empty unless you need to shift the whole device-port range for this addon instance.
- Use per-device `listen_port` only for targeted exceptions.

> Do **not** configure the same Bluetooth speaker in more than one addon variant at the same time. Port separation avoids listener conflicts, but it cannot safely share a physical speaker across multiple active addon instances.

## Multi-Speaker Setup

Each Bluetooth speaker is configured as a separate entry in
`bluetooth_devices` and appears as its own player in Music Assistant.

```yaml
bluetooth_devices:
  - mac: "AA:BB:CC:DD:EE:FF"
    player_name: "Kitchen"
  - mac: "11:22:33:44:55:66"
    player_name: "Bedroom"
    adapter: "00:1A:7D:DA:71:13"
    static_delay_ms: -500
```

**Tips for multi-speaker setups:**

- Assign distinct `player_name` values so they are easy to identify in MA.
- If speakers are on different Bluetooth adapters, specify the `adapter` MAC
  for each device to avoid contention.
- Use `static_delay_ms` to align audio timing across speakers when grouping
  them in Music Assistant multi-room mode.
- On low-power hardware (RPi 3 / armv7), enable `prefer_sbc_codec` and limit
  the number of simultaneous speakers to avoid CPU saturation.

## Web Interface

The addon includes a built-in web UI accessible from the Home Assistant
sidebar via **Ingress** (click the addon name in the sidebar).

The web interface provides:

- **Device status** — real-time connection state for each Bluetooth speaker.
- **Audio sink info** — detected PulseAudio/PipeWire sink name per device.
- **Volume control** — adjust speaker volume directly from the UI.
- **Bluetooth scanning** — discover and pair new Bluetooth devices.
- **Configuration** — edit settings without restarting (some changes require a
  restart to take effect).
- **Music Assistant integration** — connect to MA with one click via
  "Sign in with Home Assistant" (Configuration → Advanced settings). The bridge
  creates a long-lived MA API token automatically — no manual token setup needed.
- **Diagnostics** — system info, adapter status, and log viewer.

If `auth_enabled` is set to `true`, the web UI is protected by a password.
Set the password on first access through the web interface.

## Troubleshooting

### Speaker not connecting

1. Verify the speaker is paired at the host level:

   ```bash
   docker exec -it addon_local_sendspin_bt_bridge bluetoothctl
   # then run: devices
   ```

2. Confirm the MAC address in your config matches exactly (case-insensitive,
   colon-separated: `AA:BB:CC:DD:EE:FF`).
3. Check that the Bluetooth adapter is available: `hciconfig` should list your
   adapter as `UP RUNNING`.
4. Try removing and re-pairing the speaker if it was previously paired to
   another host.

### No audio

1. Check the addon Log tab for PulseAudio sink detection messages.
2. Increase `pulse_latency_msec` to `400`–`600` if the sink is found but audio
   is silent or choppy.
3. Verify the speaker is in **A2DP mode** (not HFP/HSP hands-free mode).
4. Restart the addon after the speaker is fully connected and showing in
   `bluetoothctl info <MAC>` as `Connected: yes`.

### High CPU usage

1. Enable `prefer_sbc_codec: true` to use the lightest Bluetooth audio codec.
2. In Music Assistant, set the Sendspin player output format to
   **PCM 44.1 kHz / 16-bit** (instead of higher sample rates or 24-bit).
3. Reduce the number of simultaneous speakers on armv7 / low-power hardware.

### Speaker disconnects frequently

1. Lower `bt_check_interval` (e.g. `5`) for faster reconnect detection.
2. Move the speaker closer to the Bluetooth adapter or reduce interference
   from Wi-Fi / USB 3.0 devices.
3. Enable `keepalive_silence: true` on the device to prevent the speaker from
   entering standby during silence.
4. If using a USB Bluetooth dongle, try a different USB port (avoid USB 3.0
   ports that share the 2.4 GHz band).

### Addon fails to start

1. Confirm your HA installation type is **OS** or **Supervised**—the addon
   requires host-level D-Bus and Bluetooth access not available in Container
   or Core installations.
2. Check the Log tab for error messages about missing D-Bus or PulseAudio
   sockets.

## Known Issues & Limitations

- **Privileged mode required.** The addon needs `SYS_ADMIN`, `NET_ADMIN`, and
  `NET_RAW` capabilities for Bluetooth stack access.
- **Host network mode.** Required for mDNS auto-discovery of Music Assistant
  and for correct PulseAudio/PipeWire socket communication.
- **Initial playback delay.** The first play command may have a brief delay
  (~1–3 s) while the Bluetooth A2DP audio channel is established.
- **armv7 memory constraints.** Raspberry Pi 3 has limited RAM; running more
  than 2–3 speakers simultaneously may cause instability.
- **Speaker must be paired before starting.** The addon manages connections but
  does not handle initial Bluetooth pairing—pair speakers at the host level
  first.

## Support

- **GitHub Issues:**
  <https://github.com/trudenboy/sendspin-bt-bridge/issues>
- **Documentation:**
  <https://trudenboy.github.io/sendspin-bt-bridge>
- **Discord:** Music Assistant server —
  look for the Sendspin / Bluetooth channel
