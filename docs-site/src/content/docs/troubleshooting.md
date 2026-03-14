---
title: Troubleshooting
description: Solving common Sendspin Bluetooth Bridge problems in the current UI and deployment model
---

## Audio plays from one speaker only after reconnect

When a Bluetooth speaker disconnects and reconnects, PulseAudio may move active streams to the default sink. The bridge corrects this automatically on the next playback start, but if the issue repeats:

1. Check logs for sink routing messages.
2. Verify the expected Bluetooth sink exists.
3. Restart playback after the reconnect has fully settled.

## Music Assistant does not see the player

Check these first:

1. The Sendspin provider is enabled in Music Assistant.
2. `SENDSPIN_SERVER` points to the correct host, or `auto` discovery is allowed.
3. The bridge logs do not show startup or bind errors.
4. The per-device sendspin port is not already in use.

## Bluetooth does not connect

1. Confirm the speaker is paired at the host level.
2. Confirm D-Bus is available to the bridge.
3. Confirm the adapter is powered.
4. Try **Re-pair** from the dashboard action menu.

If you use multiple adapters, double-check that the device row is bound to the correct adapter ID or MAC.

## "No sink" or silent playback

**No sink** means Bluetooth is connected but the audio sink was not attached yet.

| Cause | What to try |
|---|---|
| Audio server not running | Check `pactl info` |
| Sink not ready yet | Wait a few seconds after BT connect |
| Wrong user/socket mapping | Verify audio socket exposure |
| Wrong profile | Check for an A2DP sink profile |

On slower systems, raise **PulseAudio latency (ms)** and consider **Prefer SBC codec**.

## Scan finds nothing

If **Scan** returns no results:

1. Put the speaker into pairing mode before starting the scan.
2. Wait for the full background scan to finish.
3. Check the on-screen error text in the discovery card.
4. Retry only after the cooldown expires.
5. Use the **Already paired** list if the host already knows the speaker.

## Empty state goes to the wrong place

The redesigned empty states should now jump directly to the correct configuration surface:

- **Scan for devices** → **Configuration → Devices → Discovery & import**.
- **Add adapter** → **Configuration → Bluetooth** with a blank adapter row ready.

If this does not happen, verify the web UI is updated to the latest release.

## Authentication problems

### MFA / TOTP step fails

When Home Assistant requires MFA, the login page switches to a dedicated verification step. If the flow fails:

1. Start from a fresh login page rather than an old bookmarked MFA step.
2. Confirm the Home Assistant user can still complete login outside the bridge.
3. Check whether the bridge session timeout is very short or the browser sat idle too long between password and TOTP entry.

### Local lockout triggered

By default, **5 failed attempts within 1 minute** triggers a **5 minute** lockout. These values are adjustable in **Configuration → Security**.

### Web UI has no auth

If you see the yellow warning banner, local auth is disabled. Use its shortcut to jump straight to **Configuration → Security** and enable protection.

## Mute or volume state does not match Music Assistant

Check the **Music Assistant** configuration tab:

- **Route volume through MA** keeps MA sliders aligned with bridge changes.
- **Route mute through MA** keeps mute state aligned with MA.

If these toggles are off, the bridge uses direct PulseAudio control for faster local response but MA may not immediately reflect the same state.

## Save vs Save & Restart vs Cancel

If configuration changes seem inconsistent:

- Use **Save** for changes that only need to persist.
- Use **Save & Restart** when runtime components need to reconnect or reinitialize.
- Use **Cancel** to discard unsaved edits and restore the last stored values in the form.

The header restart banner shows progress through save, stop, reconnect, and Music Assistant recovery phases.

## Diagnostics and bug reports

Use **Diagnostics** when you need a quick answer about:

- adapter detection,
- sink routing,
- Music Assistant health,
- per-device runtime state,
- subprocess and platform details.

Use **Download diagnostics** or **Submit bug report** before opening a GitHub issue so you have current data attached.

## Home Assistant Supervisor shows no internet or update checks fail on HAOS in Proxmox

In the current HAOS-on-Proxmox setup, the issue was traced to **MTU/path behavior**, not a Supervisor TLS-version setting. A VM NIC MTU of **1400** restored connectivity for the Supervisor internet checks.

If you see the Home Assistant Supervisor reporting no internet while the host otherwise looks healthy, inspect the VM/network MTU before chasing TLS settings.

## No sound on armv7l

If Bluetooth connects and the UI shows playback but there is still silence on armv7l, update to a release that includes the PyAV compatibility patch. Older PyAV builds on armv7l are missing the layout attribute expected by the FLAC decoder.
