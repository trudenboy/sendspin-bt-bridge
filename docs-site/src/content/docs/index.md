---
title: Sendspin Bluetooth Bridge
description: Turn Bluetooth speakers and headphones into Music Assistant players — Home Assistant addon, Docker, Raspberry Pi, and LXC deployments
hero:
  tagline: Turn Bluetooth speakers into Music Assistant players — local, headless, and multiroom-ready
  image:
    file: ../../assets/logo.svg
  actions:
    - text: Install
      link: /sendspin-bt-bridge/installation/ha-addon/
      icon: right-arrow
      variant: primary
    - text: Compare deployments
      link: '#deployment-options'
      icon: list-format
    - text: GitHub
      link: https://github.com/trudenboy/sendspin-bt-bridge
      icon: github
      variant: minimal
---

import { Aside } from '@astrojs/starlight/components';

![Sendspin Bluetooth Bridge infographic — features, architecture and deployment options](/sendspin-bt-bridge/screenshots/sbb_infographic_en.png)

## What is it?

**Sendspin Bluetooth Bridge** turns Bluetooth speakers and headphones into native [Music Assistant](https://www.music-assistant.io/) players by bridging them to the MA [Sendspin](https://www.music-assistant.io/player-support/sendspin/) protocol.

Each configured Bluetooth device shows up as its own player in Music Assistant. You can keep playback local, group rooms together, manage Bluetooth from the web UI, and run the bridge on Home Assistant, Docker, Raspberry Pi, Proxmox VE, or OpenWrt.

![Web dashboard showing multiple Bluetooth speakers with real-time playback status, volume controls and sync state](/sendspin-bt-bridge/screenshots/screenshot-dashboard-full.png)

## Why the current release line matters

<CardGrid>
  <Card title="One subprocess per speaker" icon="seti:play-list">
    The bridge uses a multi-process runtime: the main app handles Bluetooth, API, and UI, while each speaker runs in its own Sendspin daemon subprocess with dedicated audio routing.
  </Card>
  <Card title="Flexible port planning" icon="seti:terminal">
    Top-level <code>WEB_PORT</code> and <code>BASE_LISTEN_PORT</code> overrides make it easier to run multiple bridge instances or parallel HA addon tracks on one host.
  </Card>
  <Card title="Per-device listener overrides" icon="setting">
    Advanced setups can pin a player to its own <code>listen_port</code> and override the advertised host with <code>listen_host</code> when needed.
  </Card>
  <Card title="HA addon tracks" icon="seti:flag">
    Stable, RC, and beta addons use separate ingress and player-port defaults so they are easier to distinguish and safer to test side by side.
  </Card>
  <Card title="Guidance + diagnostics" icon="refresh">
    Onboarding checklists, recovery guidance, diagnostics, and SSE status updates keep headless deployments manageable.
  </Card>
  <Card title="Web UI + API" icon="laptop">
    Use the dashboard for scan/import, release or reclaim, Music Assistant reconfigure, bug reports, diagnostics, logs, and config backup/restore — or automate it through the REST API.
  </Card>
</CardGrid>

<Aside type="caution">
  You can run multiple bridge instances against the same Music Assistant server, including multiple HA addon tracks on one HAOS host. Do <strong>not</strong> configure the same Bluetooth speaker in more than one running bridge/addon at the same time.
</Aside>

## Deployment options

| | Home Assistant Addon | Docker / Raspberry Pi | Proxmox / OpenWrt LXC |
|---|---|---|---|
| Install | Addon Store | `docker compose up -d` | Host bootstrap script |
| Web UI | HA Ingress (`8080` / `8081` / `8082`) + optional direct `WEB_PORT` listener | Direct `WEB_PORT` listener (default `8080`) | Direct `WEB_PORT` listener (default `8080`) |
| Player ports | Channel default `BASE_LISTEN_PORT` (`8928+`, `9028+`, `9128+`) | `BASE_LISTEN_PORT` (default `8928+`) | `BASE_LISTEN_PORT` (default `8928+`) |
| Bluetooth stack | Host `bluetoothd` via Supervisor/runtime mounts | Host `bluetoothd` via D-Bus | Host `bluetoothd` via D-Bus bridge |
| Audio | HA audio bridge | Host PulseAudio / PipeWire | PulseAudio inside the container |
| Best for | HAOS / Supervised users | General Linux hosts and Raspberry Pi | Proxmox VE, routers, appliances |

## Multi-bridge deployment

Run multiple bridge instances against the same Music Assistant server to cover every room — each bridge handles the speakers within its Bluetooth range.

[![Multi-bridge deployment diagram with zones, adapters, and per-device players](/sendspin-bt-bridge/diagrams/deployment-multiroom.svg)](/sendspin-bt-bridge/diagrams/multiroom-diagram/)

## Start here

<CardGrid>
  <LinkCard title="Install in Home Assistant" href="/sendspin-bt-bridge/installation/ha-addon/" description="Stable, RC, and beta addon tracks; ingress and direct-listener behavior explained" />
  <LinkCard title="Install with Docker" href="/sendspin-bt-bridge/installation/docker/" description="Generic Linux host install with WEB_PORT and BASE_LISTEN_PORT overrides" />
  <LinkCard title="Install on Raspberry Pi" href="/sendspin-bt-bridge/installation/raspberry-pi/" description="Pi-specific Docker guide, pre-flight checks, and one-liner installer" />
  <LinkCard title="Install in Proxmox / OpenWrt LXC" href="/sendspin-bt-bridge/installation/lxc/" description="Native LXC deployment using the host Bluetooth stack over D-Bus" />
  <LinkCard title="Configuration" href="/sendspin-bt-bridge/configuration/" description="Bridge settings, device fields, adapters, auth, and update behavior" />
  <LinkCard title="Architecture" href="/sendspin-bt-bridge/architecture/" description="Process model, IPC, audio routing, Bluetooth lifecycle, and HA ingress behavior" />
  <LinkCard title="API Reference" href="/sendspin-bt-bridge/api/" description="REST endpoints for status, diagnostics, Bluetooth, Music Assistant, and updates" />
  <LinkCard title="Release history" href="https://github.com/trudenboy/sendspin-bt-bridge/blob/main/CHANGELOG.md" description="Current release notes, including recent UI guidance, MA auth, and Bluetooth workflow changes" />
</CardGrid>

## Community

- [MA community discussion](https://github.com/orgs/music-assistant/discussions/5061)
- [HA community thread](https://community.home-assistant.io/t/sendspin-bluetooth-bridge-turn-any-bt-speaker-into-an-ma-player-and-ha/993762)
- [Discord channel](https://discord.com/channels/330944238910963714/1479933490991599836)
