---
title: Configuration
description: Current configuration reference for the redesigned Sendspin Bluetooth Bridge setup flows
---

Sendspin Bluetooth Bridge stores persistent settings in `config.json` inside the `/config` directory. You can manage those values through the web UI, through the Home Assistant addon options, or by editing the file directly.

## Configuration surfaces

There are two main ways to manage settings:

| Surface | Best for | Notes |
|---|---|---|
| **Web UI** | Day-to-day management of devices, adapters, auth, and runtime behavior | Works for Docker, LXC, and addon installs |
| **HA addon Configuration tab** | Supervisor-managed addon options | Useful when you prefer HA-native YAML-style editing |

## Web UI configuration

![General tab of the redesigned Configuration section](/sendspin-bt-bridge/screenshots/screenshot-config.png)

The redesigned configuration area is organized into five tabs instead of one long form.

| Tab | What lives there |
|---|---|
| **General** | Bridge name, timezone, latency, web/UI ports, smooth restart, update policy |
| **Devices** | Speaker fleet table, scanning, paired-device import |
| **Bluetooth** | Adapter naming, reconnect policy, codec preference |
| **Music Assistant** | Token flows, MA endpoint, monitor, volume/mute routing |
| **Security** | Local auth, session timeout, brute-force protection |

### General tab

The **General** tab contains settings that apply to the whole bridge instance:

- **Bridge name** — appended to player names as `Player @ Name`.
- **Timezone** — with a live preview of the current local time.
- **PulseAudio latency (ms)** — higher values trade latency for stability on slower hardware.
- **Web UI port** — direct browser port for standalone installs, or an optional extra direct port in HA addon mode.
- **Base player listen port** — starting port for automatically assigned per-device sendspin listeners.
- **Smooth restart** — mutes before restart and shows restart progress.
- **Check for updates / Auto-update** — available outside HA addon mode.

If you leave the port fields empty, standalone installs default to **8080** for the web UI and **8928** for player listeners. In Home Assistant addon mode, the primary channel defaults stay fixed at **8080 / 8928** for stable, **8081 / 9028** for rc, and **8082 / 9128** for beta. Setting `WEB_PORT` to a different value in addon mode opens an **additional direct listener**; HA Ingress keeps using the fixed addon port.

### Devices tab

![Devices tab with fleet table and discovery workflow](/sendspin-bt-bridge/screenshots/screenshot-config-devices.png)

The **Devices** tab is split into two responsibilities:

- **Device fleet** — the main table for daily edits.
- **Discovery & import** — scanning nearby speakers or importing already paired devices.

Each device row can store:

| Field | Purpose |
|---|---|
| **Enabled** | Skip a device on startup without deleting it |
| **Player name** | Display name in Music Assistant |
| **MAC** | Speaker Bluetooth address |
| **Adapter** | Specific adapter binding, if needed |
| **Port** | Optional custom `listen_port`; otherwise the bridge uses `BASE_LISTEN_PORT + device index` |
| **Delay** | `static_delay_ms` latency compensation |
| **Live** | Runtime badge such as Playing, Connected, Released, or Not seen |

Advanced row details expose:

- **Preferred format** such as `flac:44100:16:2`.
- **Listen host** override (`listen_host`) for the advertised device address.
- **Keepalive interval** (`keepalive_interval`) for speakers that go to sleep aggressively.

Current runtime behavior is interval-based: any positive `keepalive_interval` enables silence keepalive, values below 30 seconds are raised to 30, and `0` or an empty field disables it. Older Home Assistant addon configs may still contain the legacy `keepalive_silence` flag, but the current bridge behavior keys off `keepalive_interval > 0`.

### Bluetooth tab

![Bluetooth tab with adapter inventory and recovery policy](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

The **Bluetooth** tab combines inventory and policy:

- Rename adapters for clearer dashboard labels.
- Add manual adapter entries when auto-detect is incomplete.
- Refresh detection without leaving the page.
- Set **BT check interval** for reconnect probing.
- Set **Auto-disable threshold** for unstable devices. When the threshold is reached, the device is persisted as disabled until you re-enable it.
- Toggle **Prefer SBC codec** to reduce CPU load.

### Music Assistant tab

![Music Assistant tab with token actions and bridge integration settings](/sendspin-bt-bridge/screenshots/screenshot-advanced-settings.png)

The **Music Assistant** tab includes both connection state and auth helpers:

- **Connection status** summary.
- **Discover** nearby MA servers or reuse a known URL.
- **Get token** with MA credentials. On success the bridge stores `MA_API_URL`, a long-lived `MA_API_TOKEN`, and `MA_USERNAME`; the password is **not** stored.
- **Home Assistant fallback** when the MA instance is HA-backed and direct MA login needs HA OAuth / MFA.
- **Get token automatically** for HA-backed MA targets. In HA Ingress the UI first attempts silent auth with the current HA browser token, then falls back to a popup-based HA login flow if needed.
- Manual **MA API token** field.
- **MA server** and **MA WebSocket port** for the sendspin endpoint.
- **WebSocket monitor**, **Route volume through MA**, and **Route mute through MA** toggles.

### Security tab

In standalone deployments, the **Security** tab controls local access to the web UI:

- **Enable web UI authentication**.
- **Session timeout** in hours (1–168).
- **Brute-force protection** toggle.
- **Max attempts**, **Window**, and **Lockout** policy fields.
- **Set password** flow for the local password hash.

Standalone login uses CSRF-protected forms plus a `SameSite=Lax`, `HttpOnly` session cookie. In Home Assistant addon mode, auth is always enforced by Home Assistant / Ingress instead, so the standalone security controls are hidden.

### Footer actions

The footer is shared across tabs:

- **Save** writes `config.json` now.
- **Save & Restart** saves and restarts immediately. Use this for port changes, auth/session changes, or anything else that must be applied at startup.
- **Cancel** restores the last saved values in the form.
- **Download** exports a share-safe timestamped JSON file with secrets such as `MA_API_TOKEN`, password hash, and secret key removed.
- **Upload** imports a previously exported config and preserves the existing password hash, secret key, and stored MA token on the server.

## Home Assistant addon options

![HA addon configuration panel with core options](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config.png)

When running as a Home Assistant addon, the Supervisor exposes a native **Configuration** tab in HA.

Go to **Settings → Add-ons → Sendspin Bluetooth Bridge → Configuration**.

Core addon options include:

| Option | Purpose |
|---|---|
| **sendspin_server** | Hostname/IP of the Music Assistant server, or `auto` for mDNS |
| **sendspin_port** | Sendspin WebSocket port, usually `9000` |
| **web_port** | Optional direct host-network web port; Ingress keeps using the fixed addon port |
| **base_listen_port** | Starting port for auto-assigned device listeners |
| **bridge_name** | Optional instance label appended to players |
| **tz** | IANA timezone |
| **pulse_latency_msec** | Audio buffer latency hint |
| **prefer_sbc_codec** | Lower-CPU codec preference |
| **bt_check_interval** | Reconnect polling interval |
| **bt_max_reconnect_fails** | Auto-disable threshold |
| **auth_enabled** | Standalone-style auth toggle for direct access; HA addon mode still enforces HA auth regardless |
| **ma_api_url / ma_api_token** | Music Assistant REST integration |
| **volume_via_ma / mute_via_ma** | Route controls through MA to keep UI state aligned |
| **update_channel** | In-app release-lane preference for update checks and warnings |
| **log_level** | Base logging verbosity |

![HA addon device and adapter lists plus device edit dialog](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config-bottom.png)

![Home Assistant addon device edit dialog](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-device-edit.png)

The addon form also exposes the full **Bluetooth devices** and **Bluetooth adapters** structures. Older addon configs may still preserve a legacy `keepalive_silence` field during translation, but current runtime behavior is driven by `keepalive_interval`.

## Port and listener strategy

### Top-level web and listen ports

The bridge now supports two optional top-level port overrides:

| Key | Applies to | Default | Notes |
|---|---|---|---|
| `WEB_PORT` | Web UI listener | `8080` outside HA addon mode | In Home Assistant addon mode, the fixed ingress listener keeps using the channel-safe addon default; a configured `WEB_PORT` opens an additional direct listener only. |
| `BASE_LISTEN_PORT` | Auto-assigned per-device Sendspin listeners | `8928` outside HA addon mode | Used as the starting port when a device does not define its own `listen_port`. |

In Home Assistant addon mode, channel defaults are intentionally separated to avoid collisions when multiple addon variants run on the same HAOS host:

| Installed addon track | Default ingress / web port | Default base listen port |
|---|---|---|
| Stable | `8080` | `8928` |
| RC | `8081` | `9028` |
| Beta | `8082` | `9128` |

Use overrides only when you need:

- a direct non-Ingress web listener in addon mode;
- a custom web port for Docker/LXC/systemd deployments;
- a different default range for many device listeners on the same host.

### Per-device listener overrides

Each Bluetooth device may also define its own `listen_host` and `listen_port`.

Use device-level overrides when:

- a single speaker must keep a stable known port;
- you are splitting devices across several bridge instances and want explicit port planning;
- you need to avoid a collision without moving the whole bridge's base range.

## `config.json` reference

### Core keys

| Key | Type | Description |
|---|---|---|
| `SENDSPIN_SERVER` | string | Music Assistant host or `auto` |
| `SENDSPIN_PORT` | integer | Sendspin WebSocket port |
| `WEB_PORT` | integer or `null` | Optional web UI port override |
| `BASE_LISTEN_PORT` | integer or `null` | Optional base port for auto-assigned device listeners |
| `BRIDGE_NAME` | string | Optional label appended to player names |
| `TZ` | string | IANA timezone |
| `PULSE_LATENCY_MSEC` | integer | Audio buffer latency hint |
| `BT_CHECK_INTERVAL` | integer | Probe interval for Bluetooth recovery |
| `BT_MAX_RECONNECT_FAILS` | integer | Auto-disable threshold |
| `PREFER_SBC_CODEC` | boolean | Lower-CPU codec preference |
| `AUTH_ENABLED` | boolean | Enable local web auth outside HA addon mode; HA addon mode always enforces auth |
| `SESSION_TIMEOUT_HOURS` | integer | Browser session lifetime |
| `BRUTE_FORCE_PROTECTION` | boolean | Enable temporary lockout after failed sign-ins |
| `BRUTE_FORCE_MAX_ATTEMPTS` | integer | Maximum failed attempts within the window |
| `BRUTE_FORCE_WINDOW_MINUTES` | integer | Rolling window for failed attempts |
| `BRUTE_FORCE_LOCKOUT_MINUTES` | integer | Lockout duration |
| `MA_API_URL` | string | Music Assistant REST URL |
| `MA_API_TOKEN` | string | Music Assistant API token |
| `MA_USERNAME` | string | Username last used for a successful MA connection flow |
| `MA_WEBSOCKET_MONITOR` | boolean | Live queue/now-playing monitor |
| `VOLUME_VIA_MA` | boolean | Route volume changes through MA |
| `MUTE_VIA_MA` | boolean | Route mute changes through MA |
| `SMOOTH_RESTART` | boolean | Mute before restart and show restart progress |
| `UPDATE_CHANNEL` | string | Update channel: `stable`, `rc`, or `beta` |
| `AUTO_UPDATE` | boolean | Allow bridge-managed auto-update where supported |
| `CHECK_UPDATES` | boolean | Enable update checks |
| `LOG_LEVEL` | string | Base log verbosity |
| `TRUSTED_PROXIES` | array | Extra proxy IPs allowed to supply trusted Ingress headers |

### Bluetooth devices

```json
{
  "BLUETOOTH_DEVICES": [
    {
      "mac": "AA:BB:CC:DD:EE:FF",
      "player_name": "Living Room Speaker",
      "adapter": "hci0",
      "static_delay_ms": -500,
      "listen_host": "0.0.0.0",
      "listen_port": 8928,
      "preferred_format": "flac:44100:16:2",
      "keepalive_interval": 60,
      "enabled": true
    }
  ]
}
```

| Field | Description |
|---|---|
| `mac` | Speaker Bluetooth MAC |
| `player_name` | Display name in Music Assistant |
| `adapter` | Adapter ID or MAC |
| `static_delay_ms` | Fixed sync compensation |
| `listen_host` | Advertised host override for this device listener |
| `listen_port` | Custom sendspin listener port; if missing, runtime uses `BASE_LISTEN_PORT + device index` |
| `preferred_format` | Preferred output format |
| `keepalive_silence` | Legacy compatibility flag from older addon configs; current runtime behavior does not expose a separate toggle in the web UI |
| `keepalive_interval` | Silence keepalive interval in seconds; any positive value enables keepalive, minimum effective interval is 30 seconds |
| `enabled` | Skip on startup when `false` |

Each effective `listen_port` must be unique across devices. If you run multiple bridge instances on the same host, either give each bridge a different `BASE_LISTEN_PORT` range or set explicit `listen_port` values per device.

### Bluetooth adapters

```json
{
  "BLUETOOTH_ADAPTERS": [
    {
      "id": "hci0",
      "mac": "C0:FB:F9:62:D6:9D",
      "name": "Living room dongle"
    }
  ]
}
```

| Field | Description |
|---|---|
| `id` | Adapter interface name such as `hci0` |
| `mac` | Adapter MAC address |
| `name` | Friendly label shown in the UI |

## Port planning and HA ingress notes

- **HA Ingress** keeps using the addon channel port even if you configure a custom `WEB_PORT`.
- **Multi-bridge setups** should use non-overlapping `WEB_PORT` and `BASE_LISTEN_PORT` ranges.
- **Per-device overrides win**: `listen_port` and `listen_host` override top-level defaults.
- **Port conflicts are fatal for the daemon**: duplicate `listen_port` values will prevent a device listener from binding.

## Environment variables

The bridge directly reads a small set of runtime/bootstrap environment variables. `CONFIG_DIR` always chooses where `config.json` lives, and `WEB_PORT` / `BASE_LISTEN_PORT` environment overrides are resolved before the stored config values.

| Variable | Description |
|---|---|
| `WEB_PORT` | Direct web UI port override. Standalone default is `8080`; addon channels keep fixed primary ports and may open this as an extra direct port |
| `BASE_LISTEN_PORT` | Starting port for auto-assigned player listeners. Stable default is `8928`, rc `9028`, beta `9128` |
| `TZ` | Timezone override used when the runtime initializes local time handling |
| `BRIDGE_NAME` | Optional bridge-name override before a stored name exists |
| `CONFIG_DIR` | Config directory path |
