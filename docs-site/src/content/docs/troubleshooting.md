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
- HA addon installs receive the **Ingress port dynamically from Home Assistant Supervisor** (the addon manifest declares `ingress_port: 0`). The bridge reads the actual port from `/addons/self/info` on startup. The legacy fallback constants `8080 / 8081 / 8082` only apply if Supervisor lookup fails.
- `WEB_PORT` set inside the addon is **ignored**: the bridge does not currently open a parallel direct listener in addon mode. Open the UI through the Home Assistant sidebar entry or the addon page's **Open Web UI** button.

In standalone installs, if a direct port does not respond, check for another service already bound to that port and use **Save & Restart** after changing the setting.

## Bluetooth not accessible on HA Supervised + Ubuntu

If the addon cannot see or control the Bluetooth adapter on **HA Supervised running on Ubuntu 24.04+**, the host AppArmor profile is likely blocking raw HCI socket and D-Bus access.

Symptoms: adapter shows as powered off, `bluetoothctl` inside the addon cannot list or pair devices, logs show permission errors for Bluetooth operations.

**Fix (v2.52.0+):** Update the addon — the AppArmor profile now includes `dbus,` and `network raw,` rules required by Ubuntu 24.04's strict defaults.

**Workaround for older versions:** On the host, temporarily set the addon container's AppArmor profile to unconfined:

```bash
sudo aa-status | grep sendspin        # confirm the profile name
sudo apparmor_parser -R /etc/apparmor.d/<profile>   # remove it
```

Then restart the addon. This is a temporary measure — updating is the correct fix.

**Standalone Docker on Ubuntu:** The `docker-compose.yml` already includes `security_opt: apparmor:unconfined, seccomp:unconfined`. If you wrote your own compose file, add those lines.

**HAOS** is not affected — it uses a minimal security policy that does not restrict Bluetooth.

## Audio stuttering and D-Bus freezes on Raspberry Pi with a USB BT dongle

**Symptoms.** On a Raspberry Pi 4 / 5 with an external USB Bluetooth dongle (e.g. ASUS USB-BT500, TP-Link UB500), playback starts cleanly but then degrades within minutes:

- audio stutters or drops out across one or more speakers;
- `btop` or other tools that walk the system D-Bus freeze for several seconds at a time;
- `iwconfig wlan0` shows the **Tx excessive retries** counter climbing rapidly (often into the thousands).

**Likely cause.** 2.4 GHz coexistence between the Pi's onboard WiFi (BCM43455 on Pi 4 / Pi 5) and the USB BT dongle. Both radios share the 2.4 GHz ISM band; when WiFi traffic and BT ACL packets contend on the same air, the BT controller starts retransmitting, BlueZ blocks on the busy controller, and any client that issues synchronous D-Bus calls (the bridge daemon, `btop`, `bluetoothctl`) stalls along with it.

**Quick diagnostic.** Watch the WiFi retry counter while playback is running:

```bash
iwconfig wlan0 | grep -i "Tx excessive retries"
```

If that number is increasing during playback, coexistence is a strong suspect.

**Fix that has been confirmed by a community report ([#212](https://github.com/trudenboy/sendspin-bt-bridge/issues/212)).** Move the Pi to a 5 GHz WiFi network if the router supports it:

```bash
nmcli connection modify "<your-wifi-name>" wifi.band a
nmcli connection up "<your-wifi-name>"
```

After the switch, `Tx excessive retries` should stay at `0` during playback and the audio dropouts and `btop` freezes should disappear.

If you cannot move the host to 5 GHz, you may need to look into wiring the Pi over Ethernet, switching the BT dongle to a different physical setup, or accepting that single-speaker playback is the practical ceiling on a 2.4 GHz-only Pi.

## Samsung Q-series soundbar refuses to pair (`AuthenticationCanceled` / "No Resources")

**Symptom.** Pairing a Samsung HW-Q910B / Q990B / similar Q-series soundbar fails immediately with:

```
hci0 1C:86:9A:** type BR/EDR connect failed (status 0x07, No Resources)
Failed to pair: org.bluez.Error.AuthenticationCanceled
```

The same speaker pairs fine with a phone or another Linux desktop, and the failure is identical regardless of which USB Bluetooth adapter you swap in.

**Root cause.** Samsung Q-series firmware **filters incoming BR/EDR connection attempts by the initiator's Class of Device**. When the local adapter advertises a CoD the speaker doesn't recognise (HAOS / Raspberry Pi default to `0x0c0000`, which fails the filter), the soundbar replies `LMP_not_accepted_ext: Limited Resources` over the air. BlueZ surfaces that as `HCI Connect Complete status=0x0d` → `MGMT Connect Failed: No Resources (0x07)` → `org.bluez.Error.AuthenticationCanceled` — a misleading name; no authentication phase ever ran.

Tracked upstream as [bluez/bluez#1025](https://github.com/bluez/bluez/issues/1025).

**Fix.** Two steps:

1. **Enable the experimental flag.** Open **Configuration → Advanced → Experimental features** and toggle on **Per-adapter Class of Device override (Samsung Q-series workaround)**. Save and restart the bridge.
2. **Set the CoD value.** Go to **Configuration → Bluetooth**, find your adapter row — the **Class of Device** dropdown is now editable. Pick **0x00010c — Computer / Laptop (Samsung Q-series)**. Save the config and restart the bridge, then re-pair the soundbar.

If the laptop preset doesn't fix it, the dropdown also exposes three other documented presets (generic Computer, A/V Loudspeaker, A/V Headset) plus a custom-hex field. See **[Class of Device override — preset reference](#class-of-device-override--preset-reference)** below for a per-value table of which speakers each one was reported to fix.

The bridge applies the override via a raw HCI `Write_Class_Of_Device` command at startup and **re-applies it immediately before each pair attempt** — so an intervening adapter power-cycle by `bluetoothd` doesn't undo the override right before the soundbar's CoD filter runs.

On a regular Docker / LXC deployment you can alternatively persist the value at the host level by adding `Class = 0x00010c` under `[General]` in `/etc/bluetooth/main.conf` and restarting `bluetooth.service` — same effect, host-wide. On HAOS the in-bridge override is the recommended path because the host's `/etc/bluetooth/main.conf` is part of the read-only OS image.

**If pairing still fails after enabling the flag.** Check the bridge log for a line like:

```
CoD: hci0 Write_Class_Of_Device returned HCI status=0x0d
```

That means the adapter rejected the HCI command (rare — observed on some virtual/emulated controllers). To verify manually whether raw HCI works on your adapter:

```bash
docker exec sendspin-client hcitool -i hci0 cmd 0x03 0x0024 0c 01 00
```

Expected output: `< HCI Command: ... > HCI Event: 0x0e ... 00`. If you see a non-zero status byte, the adapter's firmware doesn't support `Write_Class_Of_Device` and the in-bridge path won't help; try the host-level `main.conf` approach above instead.

The bridge will also surface this as a recovery card automatically when it detects the `AuthenticationCanceled` + "No Resources" fingerprint during pairing — clicking the card opens the Bluetooth tab where the dropdown lives.

## Class of Device override — preset reference

The CoD dropdown in **Configuration → Bluetooth** ships four presets plus a custom-hex field. Every preset corresponds to a documented case where that exact value unblocked a Bluetooth audio device that filters incoming connections by the initiator's Class of Device. The table below is the canonical reference — pick the preset that matches the speaker family you are trying to pair, restart the bridge, and re-pair.

If the speaker's vendor / chipset isn't listed and the laptop preset doesn't help, try the rest in the order shown (broader first), then fall back to **Custom hex** with a value derived from the [Bluetooth Core spec, Vol 4 Part E §7.3.26](https://www.bluetooth.com/specifications/assigned-numbers/) plus the [BT Class of Device reference table](https://github.com/CityofToronto/bdit_data-sources/blob/master/bluetooth/BT_ClassOfDevice.csv). Report any new value that helps in [issue #210](https://github.com/trudenboy/sendspin-bt-bridge/issues/210) so we can promote it to a preset.

| CoD hex     | Major / Minor             | Reported to fix                                              | Source |
|-------------|---------------------------|--------------------------------------------------------------|--------|
| `0x00010c`  | Computer / Laptop          | **Samsung HW-Q910B / Q990B / Q990D** Q-series soundbars; same fingerprint observed on LG SN-series clones running Samsung-derived firmware. The canonical fix from the BlueZ thread. | [bluez/bluez#1025](https://github.com/bluez/bluez/issues/1025), [issue #210](https://github.com/trudenboy/sendspin-bt-bridge/issues/210) |
| `0x000100`  | Computer / generic         | Broad fallback when the laptop variant doesn't match. Distro default in `hcid.conf` and `/etc/bluetooth/main.conf` template — older Bluetooth speakers that only check the **Major Device Class = Computer** bit accept it. Reported to make Linux PCs visible to phones / cheap speakers when the kernel default `0x0c0000` failed. | [hcid.conf(5)](https://linux.die.net/man/5/hcid.conf), [Linux Mint forum](https://forums.linuxmint.com/viewtopic.php?t=425698), [Arch BBS](https://bbs.archlinux.org/viewtopic.php?id=226633) |
| `0x000414`  | Audio/Video / Loudspeaker  | **LG TVs** and similar appliances that only allow connections from devices that themselves identify as audio targets. ArchWiki: "LG TVs (and some others) are discoverable from their audio devices, so using `000414` (the soundbar class) will make such devices appear." | [ArchWiki — Bluetooth](https://wiki.archlinux.org/title/Bluetooth), [Raspberry Pi forum (audio receiver setup)](https://forums.raspberrypi.com/viewtopic.php?t=161944) |
| `0x240404`  | Audio/Video / Headset audio | **Anker Soundcore family** (Life P2, Q20, Q45, Space series) — these speakers report themselves with this exact CoD and a few firmware revisions reciprocate by accepting connections only from initiators in the same A/V class. Used for symmetric-CoD acceptance bugs. | Anker Soundcore PulseAudio output (`bluez.class = "0x240404"`) reported on [Linux Mint](https://forums.linuxmint.com/viewtopic.php?t=417263) and [Manjaro](https://forum.manjaro.org/t/anker-soundcore-life-q20-bluetooth-headset-doesnt-show-up-as-output-input-device/35914) forums |

### Other CoD values worth knowing about

These are spec-valid but **not** in the dropdown — we don't have public bug reports proving they help, so they're available via the **Custom hex** field if the four presets fall short. Listed for completeness so support tickets can reference them.

| CoD hex     | Major / Minor                | Why you might try it |
|-------------|------------------------------|---------------------|
| `0x000104`  | Computer / Desktop            | Same Major as `0x00010c` but Desktop minor — try when the speaker filters on the full Major+Minor pair and rejects "Laptop" specifically. |
| `0x000108`  | Computer / Server             | Enterprise soundbar / conferencing dock filters that reject consumer device classes. |
| `0x000400`  | Audio/Video / Uncategorized   | Generic A/V — a few cheap receivers expect *any* A/V class without caring about the minor. |
| `0x000418`  | Audio/Video / Headphones      | Equivalent of `0x000414` but headphone-class — useful if the speaker filters Loudspeaker minor specifically. |
| `0x200404`  | A/V / Headset audio + service bit `Audio` set | Same Major+Minor as `0x000404` plus the **Audio** service bit (bit 21). Some firmware checks both fields. |
| `0x200414`  | A/V / Loudspeaker + service bit `Audio` set   | `0x000414` with the Audio service bit — used in some [Raspberry Pi A2DP-receiver guides](https://forums.raspberrypi.com/viewtopic.php?t=161944). |
| `0x240414`  | A/V / Loudspeaker + service bits `Audio` and `Rendering` | Belt-and-braces variant for speakers that check service bits. |
| `0x340404`  | A/V / Wearable Headset         | Documented for **Kenwood KCA-BT300** in the [BT Class of Device CSV](https://github.com/CityofToronto/bdit_data-sources/blob/master/bluetooth/BT_ClassOfDevice.csv) — relevant for car-audio Bluetooth bridges. |
| `0x340408`  | A/V / Hands-free               | Documented for **Kenwood BT Adapter KCA-BT200** in the same reference. |

The CoD field is a 24-bit value structured as `[service classes (11 bits)][major class (5 bits)][minor class (6 bits)][format type (2 bits)]`. The format-type field (lowest 2 bits) is always `00` for Bluetooth Core 5.x devices, so all valid values end in a hex digit divisible by 4 (`0`, `4`, `8`, `c`).

### How the override is applied

When you save a non-default CoD value, the bridge:

1. Sends a raw HCI `Write_Class_Of_Device` command (OGF=0x03, OCF=0x0024) on a `BTPROTO_HCI` socket bound to the chosen controller — same capability surface (`CAP_NET_RAW`) the AVRCP HCI monitor and live-RSSI readout already use.
2. Re-applies the value immediately before each outbound pair attempt, so an intervening adapter power-cycle by `bluetoothd` (or by the BlueZ adapter-recovery ladder) doesn't undo the override right before the speaker's CoD filter inspects the initiator.
3. Reads the live CoD back via `HCI_Read_Class_Of_Device` so the **Configuration → Bluetooth** adapter row can show the live value next to the configured one — green when they match.

If the controller's firmware rejects the write (`Write_Class_Of_Device returned HCI status=0x0d` in the bridge log) the in-bridge path can't help; persist the value at the host level instead by adding `Class = 0x00010c` (or whichever value you want) under `[General]` in `/etc/bluetooth/main.conf` and restarting `bluetooth.service`. On HAOS the host's `main.conf` is part of the read-only OS image, so the in-bridge override is the only path — adapters that reject the HCI write on HAOS aren't currently fixable without a host-level shell.

## Bluetooth does not connect

1. Confirm the speaker is paired at the host level.
2. Confirm D-Bus is available to the bridge.
3. Confirm the adapter is powered and not RF-killed (`rfkill list bluetooth`).
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

## Speaker pairs but no audio sink appears (BlueZ 5.86 regression)

**Symptom.** The speaker pairs and `bluetoothctl` reports `Connected: yes`, but `pactl list cards short` shows no `bluez_card.<MAC>` for it and `pactl list sinks short` has no `bluez_sink.<MAC>.a2dp_sink` / `bluez_output.<MAC>.*`. After about 30–40 seconds the speaker drops the link on its own, and subsequent reconnect attempts fail until the speaker is power-cycled.

**Root cause.** Upstream BlueZ regression tracked in [bluez/bluez#1922](https://github.com/bluez/bluez/issues/1922) (see also [bluez/bluez#1898](https://github.com/bluez/bluez/issues/1898)): for **dual-role** devices (speakers that also expose an A2DP source or HFP/HSP — two-way speakerphones, TWS-capable speakers, smart-assistant devices) BlueZ 5.86 fails to register the A2DP Sink profile during Connect(). The bridge can confirm the link on D-Bus but the sink is never created by PulseAudio, because from its perspective no A2DP-sink-capable card appeared.

**Fixed in upstream** by commit `066a164` ("a2dp: connect source profile after sink"), shipped in `bluez 5.87` and back-ported to `5.86-4.1` in some distributions.

**What the bridge does automatically.** Since v2.60.2 the bridge applies two workarounds on every connect:

1. After a successful generic `Connect()`, it explicitly calls `Device1.ConnectProfile(A2DP_SINK_UUID)` to force BlueZ to offer the sink profile. On a healthy stack this is a no-op.
2. If no sink appears after the discovery retries, it performs a one-shot disconnect → 2 s wait → reconnect "dance", which is often enough to make the second Connect() register the profile correctly.

If these fallbacks do not help, you are on a confirmed 5.86-regressed stack and need a host-level fix.

**Host-level workaround: disable HFP/HSP in BlueZ.** With no HFP/HSP profile available, BlueZ has no choice but to negotiate A2DP. Edit the host's `/etc/bluetooth/main.conf`:

```ini
[General]
DisablePlugins=hfp,hsp
```

Then restart the BlueZ daemon (`systemctl restart bluetooth` on a regular host). On HAOS this file lives inside the host layer and cannot be edited from inside the addon — you would need a host-level SSH session, and the change does not survive a HAOS upgrade.

**Host-level workaround: swap the Bluetooth adapter.** Some built-in controllers (notably Intel AX200/AX210) are more affected by the 5.86 regression than simpler USB dongles. Moving the affected speaker onto a CSR8510 or Realtek-based USB dongle (passed through to the HAOS VM / LXC) often avoids the code path entirely. This is worth trying when you cannot update the host BlueZ in the short term.

**Proper fix.** Update the host OS to a release that ships `bluez ≥ 5.87` or a patched `5.86-4.1`. On HAOS this happens automatically as part of a Supervisor/host update. Once the patched version is running, no bridge-side intervention is needed.

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

## Speaker hardware buttons hit the wrong speaker

**Symptom.** With two or more speakers on the **same** Bluetooth adapter, pressing **Next**, **Previous**, **Play**, or **Pause** on speaker A actually skips, plays, or pauses the music on speaker B.

**Root cause.** BlueZ forwards every AVRCP target event to its first registered media player without telling the listener which **ACL connection** the keypress arrived on. From BlueZ's point of view there is only one MPRIS player to address, so all hardware buttons collapse onto the most recently active speaker.

**What the bridge does automatically.** The bridge runs an HCI source monitor (`services/hci_avrcp_monitor.py`) that opens a raw `HCI_CHANNEL_MONITOR` socket, parses the AVRCP passthrough opcodes coming through the controller, and maps each keypress back to the actual ACL connection handle (and therefore the actual speaker MAC). The dispatcher then forwards the command to that speaker's MPRIS player only.

**When this falls back.** The HCI monitor needs `CAP_NET_RAW` inside the container. If it cannot open the monitor socket — typically a stripped Docker setup or a tightly seccomp-confined LXC — the bridge logs a warning at startup and routes every AVRCP keypress to the **most recently active** speaker on that adapter. That is good enough for one-speaker-per-adapter setups but produces the wrong-speaker symptom above as soon as you have two.

**How to verify.**

1. Open **Diagnostics → Advanced → Subprocess and runtime info** (or run `docker logs sendspin-client | grep -i hci_avrcp`).
2. Look for `hci_avrcp_monitor: started on hciN` (good) vs. `hci_avrcp_monitor: missing CAP_NET_RAW, falling back to default-client routing` (degraded).
3. If the monitor never started, add the missing capability:
   - **Docker**: ensure the `cap_add: [NET_ADMIN, NET_RAW, SYS_ADMIN]` block is present in `docker-compose.yml`.
   - **HA addon**: the addon image already requests these caps; verify in **Supervisor → Addons → Sendspin BT Bridge → Info**.
   - **LXC**: confirm `lxc.cap.drop: cap_net_raw` is **not** set in the container config.

**When you do not need this.** A multi-adapter layout (each speaker on its own `hciN`) avoids the underlying ambiguity entirely — BlueZ then sees one speaker per adapter and the default-client fallback is correct. Hardware-button routing is only ambiguous when two or more speakers share one adapter.

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
