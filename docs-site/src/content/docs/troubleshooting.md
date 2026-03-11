---
title: Troubleshooting
description: Solving common issues with Sendspin Bluetooth Bridge
---

## Audio plays from one speaker only

When a Bluetooth speaker disconnects and reconnects, PulseAudio's `module-rescue-streams` automatically moves all active audio streams to the default sink. On reconnect those streams don't return automatically.

**Self-healing**: the bridge corrects this automatically. On the next playback start after a reconnect, it detects any misrouted streams and moves them back to the correct speaker. You may see a brief log message like `Corrected 1 sink-input(s) to bluez_sink.XX_XX...`.

If it keeps happening consistently:
1. Check logs: `docker logs sendspin-client | grep -i "sink\|routing\|corrected"`
2. Verify the BT sink is properly detected: `docker exec sendspin-client pactl list sinks short`
3. Try restarting the container after the speaker reconnects

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

## BT scan returns no result

If the scan UI shows a job ID and keeps polling without results, or shows an error, check:

1. The scan runs for ~10 s in the background — wait for it to complete before retrying
2. If the error text is shown in the scan dialog, it contains the reason (e.g. `bluetoothctl timed out`)
3. Verify bluetoothctl is accessible: `docker exec -it sendspin-client bluetoothctl list`
4. Try restarting the container — a stale D-Bus session can block scanning

## Pause button for a device does not work

The per-device pause button matches the player by `player_name` via D-Bus. If it has no effect:

1. Confirm the `player_name` in `config.json` exactly matches the name shown in the web UI (case-sensitive)
2. Check that the sendspin process is running: `docker exec sendspin-client ps aux | grep sendspin`
3. Check logs for D-Bus errors: `docker logs sendspin-client | grep -i "dbus\|pause"`

## Authentication issues

### "Authentication service unavailable"

When using HA login via MA, the bridge requests a `login_flow` from Home Assistant. If the response contains an unexpected `flow_id` format, authentication will fail.

1. Ensure MA is connected: check the green "MA: Connected" badge in the header
2. Verify HA is reachable from the bridge: `docker exec sendspin-client curl -s http://homeassistant:8123/api/ | head`
3. Restart the bridge — the MA token may have expired

### Brute-force lockout

After **5 failed login attempts**, the IP is locked out for **5 minutes**. This applies to all auth methods (MA, HA, Password).

Wait 5 minutes, or restart the container to clear the lockout counter.

### MA token not obtained

If `MA_API_URL` is set but `MA_API_TOKEN` is empty:
1. Use the "Sign in with Home Assistant" or "Sign in with Music Assistant" buttons in the Configuration section
2. Check logs for `MA auth` messages: `docker logs sendspin-client | grep -i "ma auth\|token"`
3. Verify the MA server URL is reachable from the bridge

## Mute not syncing

If muting in the web UI doesn't reflect in Music Assistant (or vice versa):

1. Check `MUTE_VIA_MA` setting in Configuration → Music Assistant Integration
2. When `MUTE_VIA_MA` is **disabled** (default): mute goes directly to PulseAudio — instant but not visible in MA
3. When `MUTE_VIA_MA` is **enabled**: mute is routed through MA API — synced with MA but may have slight delay
4. Verify MA connection: the header should show "MA: Connected"

## Restart phases explained

When you click **Save & Restart**, the bridge performs a phased restart showing progress for each device:

| Phase | Label | What happens |
|---|---|---|
| 1 | BT | Bluetooth reconnection |
| 2 | PA | PulseAudio sink discovery |
| 3 | SS | Sendspin subprocess start |
| 4 | MA | Music Assistant sync |

If a phase is stuck, check logs for errors related to that subsystem.

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

## No sound on armv7l (ARM 32-bit)

**Symptom:** Bluetooth connected, web UI shows "playing", but complete silence. Logs show `Audio worker is not running` errors.

**Cause:** PyAV 12.3.0 (the only version that compiles on armv7l) lacks the `AudioLayout.nb_channels` attribute that the sendspin FLAC decoder uses. The audio worker thread crashes on the first FLAC frame.

**Fix:** Update to v2.16.0+ — a monkey-patch in `services/daemon_process.py` automatically adapts the FLAC decoder for PyAV <13. Run the upgrade script:

```bash
# Inside the LXC container
bash <(wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/upgrade.sh)
systemctl restart sendspin-client
```

**Verify:** Check logs — there should be zero `Audio worker is not running` or `daemon stderr` lines:

```bash
journalctl -u sendspin-client --since "30 sec ago" | grep -E "Audio worker|daemon stderr"
```
