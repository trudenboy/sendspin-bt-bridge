---
title: Troubleshooting
description: Solving common issues with Sendspin Bluetooth Bridge
---

## Music Assistant doesn't see the player

1. Sendspin provider is enabled in MA: Settings → Providers
2. `SENDSPIN_SERVER` is correct (or `auto` mDNS works — requires `network_mode: host`)
3. Check for errors: `docker logs sendspin-client | grep ERROR`

## Bluetooth won't connect

1. Device is paired: `bluetoothctl devices` should show your MAC
2. D-Bus is accessible: `/var/run/dbus` is mounted in the container
3. Adapter is powered: `bluetoothctl show` → `Powered: yes`

Re-pair via the **🔗 Re-pair** button in the web UI.

```bash
docker exec -it sendspin-client bluetoothctl show
docker exec -it sendspin-client bluetoothctl devices
```

## No audio (No Sink)

**No Sink** means BT is connected but no PulseAudio/PipeWire sink was found.

| Cause | Fix |
|---|---|
| PulseAudio not running | `docker exec sendspin-client pactl info` |
| Sink not yet initialized | Wait 5–10 s after BT connects |
| Wrong audio socket UID | Set `AUDIO_UID` to your user's UID (`id -u`) |
| A2DP profile not loaded | `pactl list cards` — check for `a2dp-sink` profile |

## Audio stutters

1. Increase `PULSE_LATENCY_MSEC` (try 400–600)
2. Enable `PREFER_SBC_CODEC: true`
3. In MA set Audio Quality → PCM 44.1kHz/16-bit (eliminates FLAC decoding)
4. Check CPU: `docker stats sendspin-client`

## Web UI doesn't open via HA

- Addon version must be ≥ 1.4.1 (HA Ingress fix)
- Check browser console for CSS/JS 404 errors

## Collecting logs for a bug report

```bash
# Docker
docker logs sendspin-client > bridge.log 2>&1

# LXC / systemd
journalctl -u sendspin-client --no-pager > bridge.log
```

Include in your [bug report](https://github.com/trudenboy/sendspin-bt-bridge/issues):
- Deployment method (Docker/HA/LXC)
- Log output
- Host OS, audio system (PipeWire/PulseAudio), Bluetooth adapter model
- Version from `/api/version`
