---
title: Sendspin Bluetooth Bridge
description: Bluetooth bridge for Music Assistant — connects Bluetooth speakers to the Sendspin protocol
template: splash
hero:
  tagline: Connect Bluetooth speakers to Music Assistant — no extra hardware, no cloud
  image:
    file: ../../../../assets/logo.svg
  actions:
    - text: Install
      link: /sendspin-bt-bridge/en/installation/ha-addon/
      icon: right-arrow
      variant: primary
    - text: GitHub
      link: https://github.com/trudenboy/sendspin-bt-bridge
      icon: github
      variant: minimal
---

import { Card, CardGrid, LinkCard } from '@astrojs/starlight/components';

## What is it?

**Sendspin Bluetooth Bridge** is a bridge between [Music Assistant](https://www.music-assistant.io/) and Bluetooth speakers. It runs the `sendspin` CLI player as a subprocess, manages Bluetooth connections via `bluetoothctl`, and provides a web interface for monitoring and configuration. Runs on Raspberry Pi, in Home Assistant, Docker, and Proxmox LXC.

## Features

<CardGrid>
  <Card title="Multi-device" icon="list-format">
    Connect multiple Bluetooth speakers simultaneously. Each appears as its own player in Music Assistant.
  </Card>
  <Card title="Auto-reconnect" icon="refresh">
    Connection monitoring every 10 s. Automatic reconnection on disconnect.
  </Card>
  <Card title="Web UI" icon="laptop">
    Dashboard styled after Home Assistant. Volume, pause, BT adapter controls. Automatic dark/light theme.
  </Card>
  <Card title="PipeWire & PulseAudio" icon="setting">
    Auto-detects the host audio system. Both systems work without manual configuration.
  </Card>
  <Card title="Group controls" icon="bars">
    Volume and mute controls across all players simultaneously from the web UI.
  </Card>
  <Card title="Latency compensation" icon="seti:clock">
    `static_delay_ms` field compensates A2DP buffer latency for synchronized group playback.
  </Card>
</CardGrid>

## Deployment Options

| | Home Assistant Addon | Docker Compose | Proxmox LXC |
|---|---|---|---|
| Install | HA Addon Store | `docker compose up` | One-line script |
| Bluetooth | Host bluetoothd via D-Bus | Host bluetoothd via D-Bus | Own bluetoothd |
| Audio | HA Supervisor bridge | Host PulseAudio/PipeWire | Own PulseAudio |
| Config | HA panel + web UI | Web UI at :8080 | Web UI at :8080 |

<CardGrid>
  <LinkCard title="Install: Home Assistant Addon" href="/sendspin-bt-bridge/en/installation/ha-addon/" />
  <LinkCard title="Install: Docker Compose" href="/sendspin-bt-bridge/en/installation/docker/" />
  <LinkCard title="Install: Proxmox LXC" href="/sendspin-bt-bridge/en/installation/lxc/" />
  <LinkCard title="Configuration" href="/sendspin-bt-bridge/en/configuration/" />
</CardGrid>
