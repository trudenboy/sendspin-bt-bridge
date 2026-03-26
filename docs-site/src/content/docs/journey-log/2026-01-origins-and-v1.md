---
title: "January – March 1: Origins and v1 feature explosion"
description: "Architecture v0, multi-device support, HA addon, and the first 130 commits"
---

## January 2026 — Architecture v0: one file, one speaker

**Code state:** a single `sendspin_client.py` file ≈ 400 lines.

The scheme is as simple as it gets:

```
MA Server ──(WebSocket/Sendspin)──► sendspin CLI ──(PulseAudio)──► bluetoothctl ──► BT Speaker
```

The Bluetooth manager polls the connection once every 10 seconds via `bluetoothctl info <MAC>`. A disconnect is detected with up to 10 s of lag. The web interface shows minimal status; nothing is configurable.

The first PRs from the parent repository add real D-Bus monitoring to replace the timer-based ping — BT status updates instantly on a system event.

**The key limitation of this phase:** no support for multiple speakers. In PulseAudio there is a single `PULSE_SINK` — wherever `sendspin` sends its audio is where it goes. Two speakers = ambiguity.

---

## February 27 – March 1, 2026 — Feature explosion (v1.0–1.7, ~130 commits in 3 days)

The most rapid period of development. 73 commits on February 28 alone.

### Multi-device support and the HA addon (February 28)

**Repository renamed** from `sendspin-client` to `sendspin-bt-bridge` — the name reflects the new role: not a client, but a bridge.

Key additions in a single day:

- **Multi-device**: each entry in `BLUETOOTH_DEVICES` in the config launches its own `BluetoothManager` + `SendspinClient` pair. Multiple independent players appear in MA.
- **Home Assistant addon** (`ha-addon/`): manifest, Dockerfile, `run.sh`. The bridge integrates into the HA Ingress panel; the theme is injected via the postMessage API.
- **Proxmox LXC**: `proxmox-create.sh` deploys a native container in a single command. Inside — its own `bluetoothd` via D-Bus bridge, `pulseaudio --system`, `avahi-daemon`.
- **Full-featured web interface**: device cards, BT scanning, volume control, reconnect/re-pair buttons, diagnostics.
- **BT adapter management**: auto-detection, manual selection, binding a speaker to a specific `hci`.

### Player identification in MA (March 1, v1.3.x)

The primary goal was supporting multiple bridge instances connected to a single MA server. When two bridges register a player with the same name — for example `"Living Room"` — MA cannot tell them apart by name: when the second one appeared it would reset the queue of the first or confuse them. The `player_id` must be globally unique and stable regardless of the player name.

Solution: **UUID5 from the MAC address** (`v1.3.0`). The UUID is deterministic (identical on every restart), globally unique (the MAC is physically unique), and independent of the player name. Two bridges with different speakers → two different `player_id` values → MA sees them as completely independent players, even if their names are identical.

This also solved a secondary but equally noticeable problem: previously MA would lose the player on bridge restart or rename — queues and groups would reset. After v1.3.0 the `player_id` never changes.

In parallel — **MPRIS D-Bus integration** (v1.3.16): the bridge registers itself as a MediaPlayer2 object on the session bus. MA can read playback status and control the player via the standard interface. When the service stops, an MPRIS `Pause` is sent first — MA correctly stops the group before the player disappears from the network.

**Player identification in MA groups** (v1.3.19): the problem is that MA builds syncgroups by player name. Logic was added to ensure `BRIDGE_NAME` + suffix + MPRIS Identity match — so the player name in MA matches the MPRIS object name; otherwise the group doesn't form.

### Redesigned UI in HA/MA style with theme support (March 1, v1.3.7)

Before this version, the web interface looked like a generic dashboard: purple gradient header (`#667eea`), hard-coded HSL colours, system font. When opened through HA Ingress it was visually out of place in the ecosystem.

In v1.3.7 the UI is fully rewritten to match the visual language of Home Assistant and Music Assistant:

**CSS custom properties instead of hard-coded values**

```css
/* before */
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
color: #28a745;

/* after */
background: var(--app-header-background-color, #03a9f4);
color: var(--success-color, #4CAF50);
```

All colours go through HA design tokens (`--primary-color`, `--error-color`, `--success-color`, `--warning-color`, `--ha-card-border-radius`, `--ha-card-box-shadow`). The header is styled as an HA `app-toolbar`. Font: Roboto (the same one HA uses).

**Dual theming: media query + Ingress postMessage**

```css
/* static theme — works everywhere */
@media (prefers-color-scheme: dark) {
  :root { --primary-background-color: #111; ... }
}
```

```javascript
// live theme injection from HA — Ingress only
window.addEventListener('message', (e) => {
  if (e.data?.type === 'setTheme') applyTheme(e.data.theme);
});
```

When the user opens the UI through the HA sidebar, HA sends a `postMessage` with the current theme. Switching the theme in HA → instantly reflected in the web UI without a page reload. If the UI is opened directly (not through Ingress) — the theme is determined by the system `prefers-color-scheme`.

**Result:** from v1.3.7 the web interface is visually indistinguishable from native HA panels. Users who add the bridge to the HA sidebar see a consistent design.

Subsequent UI iterations (v2.6.5, v2.6.6, v2.7.x) continued polishing: track progress bar, transport controls, album art, hover actions, animated BT scan, mobile adaptation, UX audit with 20 improvements (v2.10.x).

### Security and reliability (March 1–2, v1.4–1.7)

- **Modularisation** (v1.4.0): monolithic `sendspin_client.py` split into `config.py`, `mpris.py`, `bluetooth_manager.py`.
- **Documentation site** (v1.4.2): Astro Starlight, bilingual (EN/RU), deployed to GitHub Pages.
- **Web interface authentication** (v1.6.0): PBKDF2-SHA256 for standalone mode; in the HA addon — proxied through HA Core login_flow with 2FA/TOTP support.
- **D-Bus BT monitor** (v1.7.0): switched from polling to event-driven Bluetooth monitoring — the bridge learns of a disconnect at the moment of the event, not after 10 seconds.
- **Configurable BT check interval** and auto-disable after N failed reconnect attempts.

---
