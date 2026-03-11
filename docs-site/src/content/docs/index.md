---
title: Sendspin Bluetooth Bridge
description: Turn any Bluetooth speaker into a Music Assistant player — multiroom audio without new hardware
hero:
  tagline: Turn your Bluetooth speakers into Music Assistant players — no new hardware, no cloud
  image:
    file: ../../assets/logo.svg
  actions:
    - text: Install
      link: /sendspin-bt-bridge/installation/ha-addon/
      icon: right-arrow
      variant: primary
    - text: Configuration
      link: /sendspin-bt-bridge/configuration/
      icon: setting
    - text: GitHub
      link: https://github.com/trudenboy/sendspin-bt-bridge
      icon: github
      variant: minimal
---


![Sendspin Bluetooth Bridge infographic — features, architecture and deployment options](/sendspin-bt-bridge/screenshots/sbb_infographic_en.png)

## What is it?

You probably already have Bluetooth speakers — a portable speaker in the kitchen, wireless headphones, a soundbar in the bedroom. **Sendspin Bluetooth Bridge** lets you use all of them in [Music Assistant](https://www.music-assistant.io/) without buying any new hardware.

Once installed, each Bluetooth speaker appears as a regular player in Music Assistant — just like a Sonos or Chromecast device. You can play music on one speaker, sync several rooms at once, or control everything from your phone or Home Assistant dashboard.

It runs entirely on your local network: no cloud accounts, no subscriptions, no internet connection required for playback.

![Web dashboard showing 6 Bluetooth speakers with real-time playback status, volume controls and sync state](/sendspin-bt-bridge/screenshots/screenshot-dashboard-full.png)

## What you need

- A Raspberry Pi, a PC running Home Assistant, or any Linux machine on your home network
- A Bluetooth adapter (most Raspberry Pi models have one built in)
- One or more Bluetooth speakers

## Features

<CardGrid>
  <Card title="Any Bluetooth speaker" icon="laptop">
    Works with any A2DP speaker — portable, desktop, soundbar, wireless headphones. No brand restrictions.
  </Card>
  <Card title="Multiple speakers at once" icon="list-format">
    Connect several speakers simultaneously. Each appears as its own player in Music Assistant — play different tracks in different rooms, or group them for synchronized audio.
  </Card>
  <Card title="Stays connected" icon="refresh">
    Detects disconnections instantly via D-Bus (falls back to 10 s polling when D-Bus is unavailable) and reconnects automatically — no manual intervention needed.
  </Card>
  <Card title="Synchronized multiroom" icon="seti:clock">
    Speakers can be grouped in Music Assistant for simultaneous playback. Latency compensation (`static_delay_ms`) keeps them in sync even across different A2DP buffer sizes.
  </Card>
  <Card title="Web interface" icon="laptop">
    Live dashboard shows every speaker's status, track, volume and sync state. Adjust volume, mute or pause all speakers from one page — works on phone and desktop.
  </Card>
  <Card title="Home Assistant integration" icon="setting">
    Available as a native HA addon. Speakers become media players in HA — use them in automations, dashboards, voice assistants, and scenes.
  </Card>
</CardGrid>

## Usage examples

### Multiroom audio

Group two or more Bluetooth speakers in Music Assistant and play the same track in every room simultaneously. The bridge compensates for Bluetooth latency so the audio stays in sync — you won't hear an echo when walking between rooms.

**Example setup:** kitchen portable speaker + bedroom headphones + living room soundbar, all playing the same playlist, controlled from the Music Assistant app on your phone.

### Home Assistant automations

Because each Bluetooth speaker is a media player entity in Home Assistant, you can use them in any automation:

```yaml
# Play a morning briefing on the kitchen speaker at 7:30
automation:
  trigger:
    platform: time
    at: "07:30:00"
  action:
    service: media_player.play_media
    target:
      entity_id: media_player.kitchen_speaker
    data:
      media_content_id: "https://feeds.example.com/news.mp3"
      media_content_type: music
```

Other ideas:
- **Doorbell alert** — play a chime on all speakers when the doorbell rings
- **Good night routine** — fade volume to zero and pause all speakers at bedtime
- **Room presence** — start music on a speaker when you enter a room (with a motion sensor)
- **Weather announcement** — read out a TTS weather report every morning

### Headless Home Assistant machine

If your Home Assistant runs on a Raspberry Pi with a built-in Bluetooth adapter, you can use that adapter directly — no extra hardware. The bridge runs as an addon alongside HA and exposes your Bluetooth speakers to Music Assistant immediately.

<Aside type="tip">
  If you want to connect speakers in multiple rooms and your Raspberry Pi only reaches some of them, you can run the bridge on a second Raspberry Pi (or a Proxmox LXC container) elsewhere in the house and point it at the same Music Assistant server.
</Aside>

## Multi-bridge deployment

Run multiple bridge instances against the same Music Assistant server to cover every room — each bridge handles the speakers within its Bluetooth range.

[![Deployment diagram: multiroom floorplan with zones and adapters](/sendspin-bt-bridge/diagrams/multiroom-diagram.png)](/sendspin-bt-bridge/diagrams/multiroom-diagram/)


## Deployment options

| | Home Assistant Addon | Docker Compose | Proxmox LXC |
|---|---|---|---|
| Install | HA Addon Store | `docker compose up` | One-line script |
| Bluetooth | Host bluetoothd via D-Bus | Host bluetoothd via D-Bus | Own bluetoothd |
| Audio | HA Supervisor bridge | Host PulseAudio/PipeWire | Own PulseAudio |
| Config | HA panel + web UI | Web UI at :8080 | Web UI at :8080 |

<CardGrid>
  <LinkCard title="Install: Home Assistant Addon" href="/sendspin-bt-bridge/installation/ha-addon/" />
  <LinkCard title="Install: Docker Compose" href="/sendspin-bt-bridge/installation/docker/" />
  <LinkCard title="Install: Proxmox LXC" href="/sendspin-bt-bridge/installation/lxc/" />
  <LinkCard title="Configuration" href="/sendspin-bt-bridge/configuration/" />
  <LinkCard title="Architecture" href="/sendspin-bt-bridge/architecture/" description="Process model, IPC, audio routing, BT state machine, auth" />
  <LinkCard title="Project History" href="https://github.com/trudenboy/sendspin-bt-bridge/blob/main/HISTORY.md" description="Architectural evolution, milestones, v1 → v2 migration" />
  <LinkCard title="API Reference" href="/sendspin-bt-bridge/api/" />
</CardGrid>

## Community

- [MA community discussion](https://github.com/orgs/music-assistant/discussions/5061)
- [HA community thread](https://community.home-assistant.io/t/sendspin-bluetooth-bridge-turn-any-bt-speaker-into-an-ma-player-and-ha/993762)
- [Discord channel](https://discord.com/channels/330944238910963714/1479933490991599836)
