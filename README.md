# [Sendspin](https://www.sendspin-audio.com/) Bluetooth Bridge

[![GitHub Release](https://img.shields.io/github/v/release/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/releases/latest)
[![Docker Pulls](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Ftrudenboy%2Fsendspin-bt-bridge%2Fsendspin-bt-bridge&query=downloadCount&label=Docker%20Pulls&logo=docker&color=blue)](https://github.com/trudenboy/sendspin-bt-bridge/pkgs/container/sendspin-bt-bridge)
[![HA Installs](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Faddons.json&query=%24%5B%2285b1ecde_sendspin_bt_bridge%22%5D.total&label=HA%20Installs&logo=homeassistant&color=18bcf2)](https://analytics.home-assistant.io/apps/)
[![GitHub Stars](https://img.shields.io/github/stars/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/stargazers)
[![Try Demo](https://img.shields.io/badge/Try_Demo-Live-brightgreen?style=flat&logo=render)](https://sendspin-demo.onrender.com)

[Read in Russian](README.ru.md) · [Documentation](https://trudenboy.github.io/sendspin-bt-bridge/) · [Live Demo](https://sendspin-demo.onrender.com) · [Changelog](CHANGELOG.md)

Turn Bluetooth speakers and headphones into native [Music Assistant](https://www.music-assistant.io/) [Sendspin](https://www.music-assistant.io/player-support/sendspin/) players.

Sendspin Bluetooth Bridge is a local-first, headless-friendly bridge for Home Assistant, Docker, Raspberry Pi, and LXC deployments. Each Bluetooth device appears in Music Assistant as its own player, with web UI management, diagnostics, and multi-room-friendly deployment options.

## What it does

- Turns ordinary Bluetooth speakers and headphones into Music Assistant players.
- Bridges multiple devices at once, with one isolated playback subprocess per speaker.
- Adds a web UI for setup, Bluetooth pairing workflows, diagnostics, logs, and config backup.
- Supports Home Assistant addon, Docker, Raspberry Pi, Proxmox VE LXC, and OpenWrt LXC deployments.
- Helps you scale beyond one room by running multiple bridge instances against the same MA server.

![Sendspin Bluetooth Bridge infographic — features, architecture and deployment options](docs-site/public/screenshots/sbb_infographic_en.png)

## What you need

- A Bluetooth speaker or headphones — any A2DP-capable device works.
- A [Music Assistant](https://www.music-assistant.io/) server (v2.3+) with the [Sendspin provider](https://www.music-assistant.io/player-support/sendspin/) enabled.
- A Linux host with a USB or built-in Bluetooth adapter — Raspberry Pi, NUC, Proxmox VM, or a Home Assistant OS installation.

No command-line setup required. The web UI handles Bluetooth scanning, pairing, and Music Assistant configuration entirely from the browser.

## Quick start: Home Assistant

The fastest path is the Home Assistant addon.

[![Add repository to HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

1. Add the repository to Home Assistant.
2. Install **Sendspin Bluetooth Bridge** from the Add-on Store.
3. Start the addon and open the web UI from the HA sidebar.
4. Add your Bluetooth speakers and connect Music Assistant features from **Configuration → Music Assistant**.

Full Home Assistant guide: <https://trudenboy.github.io/sendspin-bt-bridge/installation/ha-addon/>

## Choose your deployment

| Deployment | Best for | Install path | Docs |
|---|---|---|---|
| **Home Assistant Addon** | HAOS / Supervised users | Add-on Store | [Open guide](https://trudenboy.github.io/sendspin-bt-bridge/installation/ha-addon/) |
| **Docker** | Generic Linux hosts | `docker compose up -d` | [Open guide](https://trudenboy.github.io/sendspin-bt-bridge/installation/docker/) |
| **Raspberry Pi** | Pi-based installs | Docker-based setup | [Open guide](https://trudenboy.github.io/sendspin-bt-bridge/installation/raspberry-pi/) |
| **Proxmox / OpenWrt LXC** | Appliances, routers, lightweight hosts | Host bootstrap script | [Open guide](https://trudenboy.github.io/sendspin-bt-bridge/installation/lxc/) |

## Key capabilities

- **Synchronized streaming** — uses the [Sendspin](https://www.music-assistant.io/player-support/sendspin/) protocol to deliver lossless audio with time-aligned playback, so grouped speakers stay in sync across rooms.
- **No console required** — scan for Bluetooth devices, pair them, and connect to Music Assistant entirely from the web UI. No `bluetoothctl`, no config files, no SSH.
- **Deep Music Assistant integration** — now playing, album art, transport controls, group volume, shuffle and repeat — all synced in real time through a persistent connection to the MA server.
- **Home Assistant automations** — every Bluetooth speaker becomes a Music Assistant player entity visible in HA. Use it in automations, scripts, scenes, dashboards, and with voice assistants.
- **Reliable Bluetooth** — automatic reconnection, disconnect detection, and device health monitoring keep your speakers connected without manual intervention.
- **Multi-room ready** — run one bridge per room or one bridge with many speakers. Multiple bridges share the same Music Assistant server for whole-home audio.
- **Five deployment options** — Home Assistant addon, Docker, Raspberry Pi, Proxmox VE LXC, and OpenWrt LXC — same bridge, same web UI, same features everywhere.
- **REST API and live updates** — 60+ automation-friendly endpoints with real-time SSE status stream for custom dashboards and integrations.

## Documentation map

Use the docs site for the full guides and reference:

- [Installation](https://trudenboy.github.io/sendspin-bt-bridge/installation/ha-addon/)
- [Configuration](https://trudenboy.github.io/sendspin-bt-bridge/configuration/)
- [Web UI](https://trudenboy.github.io/sendspin-bt-bridge/web-ui/)
- [Devices](https://trudenboy.github.io/sendspin-bt-bridge/devices/)
- [API Reference](https://trudenboy.github.io/sendspin-bt-bridge/api/)
- [Troubleshooting](https://trudenboy.github.io/sendspin-bt-bridge/troubleshooting/)
- [Architecture](https://trudenboy.github.io/sendspin-bt-bridge/architecture/)
- [Test stand](https://trudenboy.github.io/sendspin-bt-bridge/test-stand/)

## Community and support

- [GitHub Issues](https://github.com/trudenboy/sendspin-bt-bridge/issues)
- [Music Assistant community discussion](https://github.com/orgs/music-assistant/discussions/5061)
- [Home Assistant community thread](https://community.home-assistant.io/t/sendspin-bluetooth-bridge-turn-any-bt-speaker-into-an-ma-player-and-ha/993762)
- [Discord channel](https://discord.com/channels/330944238910963714/1479933490991599836)

## Project links

- [Contributing](CONTRIBUTING.md)
- [Roadmap](ROADMAP.md)
- [License](LICENSE)
- [Changelog](CHANGELOG.md)
- [History](HISTORY.md)
