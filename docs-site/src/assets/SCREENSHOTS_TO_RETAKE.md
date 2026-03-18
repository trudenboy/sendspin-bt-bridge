# Screenshots to Retake

Use the built-in local demo mode as the **primary screenshot source** for bridge/web UI documentation. It now boots into a canonical six-player stand that is stable enough for repeatable captures and rich enough for cards, diagnostics, logs, and config screenshots.

## Primary workflow: local demo screenshot stand

From the repository root:

```bash
DEMO_MODE=true python sendspin_client.py
```

Then open `http://127.0.0.1:8080/`.

### What demo mode now guarantees

- six configured demo players on every run
- one real sync group plus multiple solo players
- mixed playing / idle / disconnected states from first render
- Music Assistant connected with representative now-playing metadata and embedded artwork
- no Bluetooth, PulseAudio, or MA hardware required
- canonical fixtures override the runtime device list for that process, so local configs do not affect screenshot composition

### Canonical six-player layout

| Player | Role | Expected state | Notes for screenshots |
| --- | --- | --- | --- |
| Living Room | `Main Floor` sync group | Playing | Primary rich card: artwork, EQ bars, transport, progress |
| Kitchen | `Main Floor` sync group | Playing | Second active group member |
| Studio | `Main Floor` sync group | Idle / connected | Shows grouped-but-not-playing state |
| Office | Solo player | Playing | Rich solo player with MA metadata/artwork |
| Patio | Solo player | Idle / connected / muted | Useful for muted + paused UI states |
| Bedroom | Solo player | Disconnected | Canonical disconnected card |

Use this stand for dashboard, header, player cards, group controls, config, diagnostics, logs, and most other docs/web UI screenshots. The remaining special-case captures are:

- HA addon configuration screenshots (require a Home Assistant environment)
- login/auth screenshots (require a dedicated auth-enabled setup; demo mode still forces auth off)

### Capture tips

- Use a 1400×900 browser window for full-page/dashboard captures.
- Use 800×600 or DevTools node screenshots for individual cards/sections.
- Collapse config/diagnostics/logs sections unless the target screenshot needs them open.
- The simulator only advances track progress and slow battery drift; it does **not** randomize device roles, so you can re-open the demo and get the same composition.

---

## Audit snapshot — 2026-03-19

Canonical published assets live in `docs-site/public/screenshots/`. Current docs references were audited against that directory.

| Asset | Docs usage | Status | Notes |
| --- | --- | --- | --- |
| `screenshot-dashboard-full.png` | `index.md`, `web-ui.md`, root `README.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with the current default **list view** and an expanded now-playing row. |
| `screenshot-header.png` | `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with the live version/update badges from the current demo runtime. |
| `screenshot-group-controls.png` | `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with the current list-view toggle state. |
| `screenshot-device-card-playing.png` | `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand. |
| `screenshot-device-card-hover.png` | `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand. |
| `screenshot-config.png` | `configuration.md`, `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand. |
| `screenshot-config-devices.png` | `devices.md`, `configuration.md`, `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with all six rows visible and the current multi-adapter fixture data. |
| `screenshot-config-adapters.png` | `devices.md`, `configuration.md`, `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with the current named adapters: `Living Room USB Adapter`, `Kitchen & Patio Controller`, and `Office Desk Bluetooth`. |
| `screenshot-advanced-settings.png` | `configuration.md`, `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand. |
| `screenshot-diagnostics.png` | `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with deterministic injected diagnostics payload matching the demo fixture state. |
| `screenshot-logs.png` | `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with deterministic injected demo log lines. |
| `screenshot-empty-no-devices.png` | `devices.md`, `web-ui.md` | ✅ refreshed | Re-captured from a separate empty-config local run. |
| `screenshot-login.png` | `web-ui.md` | ⚠️ retained | Existing asset retained. This is **not** HA-only, but it still needs a dedicated auth-enabled capture path; demo mode forces auth off, and the current real auth flows do not surface the older three-tab composition from one live state. |
| `screenshot-update-modal.png` | `web-ui.md` | ✅ refreshed | Re-captured from the canonical six-player demo stand with the current demo release notes body visible. |
| `screenshot-ha-addon-config.png` | `configuration.md` | ℹ️ retained | HA-only asset; left unchanged. |
| `screenshot-ha-addon-config-bottom.png` | `configuration.md` | ℹ️ retained | HA-only asset; left unchanged. |
| `screenshot-ha-addon-device-edit.png` | `configuration.md` | ℹ️ retained | HA-only asset; left unchanged. |
| `sbb_infographic_en.png` / `sbb_infographic_ru.png` | `index.md` / `ru/index.md` | ℹ️ retained | Not part of this screenshot retake pass. |

Docs refs were re-audited after the recapture pass:

- no docs page points at a missing screenshot filename
- no filename/reference changes were required during this pass
- `screenshot-device-card.png` and `screenshot-full-dark.png` currently remain canonical-but-unused assets in `docs-site/public/screenshots/`

---

## 1. `screenshot-dashboard-full.png`

**Used in:** `web-ui.md`, `index.md`, `README.md`

**State:** Full dashboard in the current default **list view** with all **six** demo players visible: three `Main Floor` members, `Office`, `Patio`, and disconnected `Bedroom`.
- keep one active row expanded so artwork plus previous/next queue items are visible

**Capture:** Full-page screenshot at 1400px wide. Collapse config/diagnostics/logs sections.

---

## 2. `screenshot-header.png`

**Used in:** `web-ui.md`

**State:** Top header bar only. Use the local demo run so the health pills reflect the six-player stand and the update badge shows the seeded release target.

**Capture:** Crop to header bar only.

---

## 3. `screenshot-device-card-playing.png`

**Used in:** `web-ui.md`

**State:** Single device card for `Living Room` (preferred) or `Office` while actively playing.
- artwork thumbnail visible
- EQ bars visible next to player name
- group badge visible for `Living Room`, solo layout visible for `Office`
- transport row, progress bar, volume slider, and audio format visible

**Capture:** Single card, no hover state.

---

## 4. `screenshot-device-card-hover.png`

**Used in:** `web-ui.md`

**State:** Hovered `Living Room` card.
- hover details visible: MAC, WebSocket URL, adapter, MA host, sink name
- artwork preview popover open
- action buttons visible: reconnect / re-pair / release
- group/delay metadata visible

**Capture:** Card in hover state (force `:hover` in DevTools if needed).

---

## 5. `screenshot-group-controls.png`

**Used in:** `web-ui.md`

**State:** Controls bar in list-view mode with `Main Floor` available in the filter.
- player count visible
- all checkbox visible
- volume slider around 50%
- mute / pause buttons visible
- list toggle active

**Capture:** Controls bar area only.

---

## 6. `screenshot-config-adapters.png`

**Used in:** `web-ui.md`

**State:** Configuration section expanded, showing the named demo adapters.
- `Living Room USB Adapter`
- `Kitchen & Patio Controller`
- `Office Desk Bluetooth`
- refresh and add controls visible

**Capture:** Config section from top through adapters table.

---

## 7. `screenshot-config-devices.png`

**Used in:** `web-ui.md`

**State:** Devices table in config section.
- all **six** demo rows visible
- adapter column reflects the current multi-adapter fixture (`hci0` / `hci1` / `hci2`)
- preferred formats and ports are populated
- add device / scan / already paired controls visible

**Capture:** Devices table area.

---

## 8. `screenshot-diagnostics.png`

**Used in:** `web-ui.md`

**State:** Diagnostics section expanded.
- health summary cards visible
- named adapter rows visible
- per-device rows show a mix of green/amber/red states matching the canonical six-player stand

**Capture:** Full diagnostics section.

---

## 9. `screenshot-logs.png`

**Used in:** `web-ui.md`

**State:** Logs section expanded.
- ~15 lines of output visible
- include a mix of INFO and WARNING lines if possible
- filter buttons visible

**Capture:** Full logs section.

---

## 10. `screenshot-ha-addon-config.png`

**Used in:** `configuration.md` (EN + RU)

**State:** HA addon Configuration tab, Options section.
- fields visible: Music Assistant server, Sendspin port, bridge_name, timezone, PULSE_LATENCY_MSEC, PREFER_SBC_CODEC, BT_CHECK_INTERVAL, BT_MAX_RECONNECT_FAILS, auth_enabled
- `ma_api_url` and `ma_api_token` visible

**Capture:** HA addon Options panel, full height.

---

## 11. `screenshot-ha-addon-config-bottom.png`

**Used in:** `configuration.md` (EN + RU)

**State:** HA addon config — Bluetooth devices list section.
- at least 2 device entries visible with edit/delete buttons
- add button visible

**Capture:** Devices section of HA addon config.

---

## 12. `screenshot-ha-addon-device-edit.png`

**Used in:** `configuration.md` (EN + RU)

**State:** Device edit dialog open in HA addon config.
- fields visible: mac, player_name, adapter, static_delay_ms, listen_host, listen_port, enabled
- `keepalive_silence` and `keepalive_interval` visible

**Capture:** Dialog box / modal, full height.
