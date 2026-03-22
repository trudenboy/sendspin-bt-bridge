---
title: Troubleshooting
description: Solving common Sendspin Bluetooth Bridge problems in the current UI, including guidance banners, Bluetooth scan modal behavior, Music Assistant token flows, and bug reports
---

## Onboarding or recovery banners keep appearing

The bridge now has two top-level guidance surfaces:

- **Setup checklist** for first-run / empty-state onboarding.
- **Recovery guidance** for operator-facing issues that need action.

If you want less guidance noise:

1. Use **Hide checklist** to collapse the setup checklist into a compact progress summary.
2. Use **Don’t show again** if you do not want that onboarding card to return automatically.
3. Go to **Configuration → General** and turn off **Show empty-state onboarding guidance** or **Show recovery banners**.
4. If a recovery banner keeps coming back, treat that as a signal that the underlying issue is still present.

![Recovery guidance banner with actionable operator recommendations](/sendspin-bt-bridge/screenshots/screenshot-recovery-guidance.png)

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

If the device has no explicit `listen_port`, remember that the runtime uses **`BASE_LISTEN_PORT + device index`**. In multi-bridge setups, make sure those ranges do not overlap across containers/instances on the same host.

## Web UI port or Ingress access is confusing

- Standalone installs use direct browser access on `WEB_PORT` (default **8080**).
- HA addon installs always keep their primary channel port for **Ingress** (`8080` stable, `8081` rc, `8082` beta).
- In addon mode, setting `WEB_PORT` to a different value adds an **extra direct listener**; it does not move Ingress.

If a direct port does not respond, check for another service already bound to that port and use **Save & Restart** after changing the setting.

## Bluetooth does not connect

1. Confirm the speaker is paired at the host level.
2. Confirm D-Bus is available to the bridge.
3. Confirm the adapter is powered.
4. Try **Reconnect** first, then **Re-pair** if the host pairing looks stale.
5. If the device shows **Released**, click **Reclaim** before assuming pairing is broken.

If you use multiple adapters, double-check that the device row is bound to the correct adapter ID or MAC.

If the bridge repeatedly fails to reconnect the same speaker, the configured **Auto-disable threshold** can persist that device as disabled. Re-enable it in **Configuration → Devices** after you fix the pairing, signal, or adapter problem.

If the host-level record itself is broken, use the repair/reset tools from **Configuration → Bluetooth → Paired devices** and then import or pair the speaker again.

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

If **Scan nearby** returns no useful results:

1. Open **Configuration → Bluetooth** and make sure you are scanning from the correct bridge instance.
2. Put the speaker into pairing mode before starting the scan.
3. Pick the right adapter in the scan modal, or switch to **All adapters**.
4. Leave **Audio devices only** enabled for normal speaker discovery.
5. Wait for the full timed scan to finish instead of closing the modal early.
6. Retry only after the cooldown expires and **Rescan** becomes available again.
7. Use the **Already paired devices** list if the host already knows the speaker.
8. Turn off **Audio devices only** only when you specifically need to inspect non-audio Bluetooth candidates.

## Music Assistant token flow fails

If **Get token automatically** or **Get token** does not complete:

1. Open **Configuration → Music Assistant**.
2. If the bridge already shows **Connected**, click **Reconfigure** first so the auth card reopens.
3. Confirm the MA URL is correct and reachable.
4. In HA Ingress, refresh the page from Home Assistant so the browser has a valid HA session/token.
5. Remember that **Auto-get token on UI open** only works for the **HA addon UI under HA Ingress**. Direct-port sessions and standalone installs should use the visible token flow instead.
6. Allow popups for the bridge page; the fallback HA auth flow opens a popup or MFA step when silent auth is not enough.
7. If MA is HA-backed and built-in MA login rejects the credentials, retry and complete the HA MFA step instead of expecting a pure MA-password flow.
8. Remember that the bridge stores the long-lived MA token, but not the password you entered.

## Empty-state shortcut goes to the wrong place

The current empty states should now jump directly to the correct configuration surface:

- **Scan for devices** → **Configuration → Bluetooth** with the **Scan nearby** modal opened.
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

Standalone login also depends on restart-applied settings such as auth enablement and session timeout. If you changed those values, use **Save & Restart** before concluding that the setting did not apply.

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

Use **Submit bug report** when you want the bridge to prefill the issue description for you. The dialog now opens with a **diagnostics-generated suggested description**, plus the full auto-attached diagnostics payload in an expandable preview. Edit the summary before submitting if it needs more real-world context.

Use **Download diagnostics** when you want a file without opening the GitHub issue flow.

## Home Assistant Supervisor shows no internet or update checks fail on HAOS in Proxmox

In the current HAOS-on-Proxmox setup, the issue was traced to **MTU/path behavior**, not a Supervisor TLS-version setting. A VM NIC MTU of **1400** restored connectivity for the Supervisor internet checks.

If you see the Home Assistant Supervisor reporting no internet while the host otherwise looks healthy, inspect the VM/network MTU before chasing TLS settings.

## No sound on armv7l

If Bluetooth connects and the UI shows playback but there is still silence on armv7l, update to a release that includes the PyAV compatibility patch. Older PyAV builds on armv7l are missing the layout attribute expected by the FLAC decoder.
