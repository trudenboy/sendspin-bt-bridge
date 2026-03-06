# Screenshots to Retake

All screenshots were made for v2.6.x. UI has significantly changed in v2.7–v2.10. Retake with a running instance having ≥ 2 active devices in a sync group plus ≥ 1 solo device.

## Prerequisites

- Bridge running at v2.10.6+
- At least 3 Bluetooth devices configured:
  - 2 devices in an MA sync group (e.g. "Living Room" + "Kitchen"), both **playing**
  - 1 solo device (e.g. "Bedroom"), stopped and Bluetooth-disconnected
- MA API connected (`MA_API_URL` + `MA_API_TOKEN` configured) so the `api` badge is visible
- Album cover art visible for the playing devices

---

## 1. `screenshot-dashboard-full.png`

**Used in:** `web-ui.md`, `index.md`, `README.md`

**State:** Full dashboard, all 3 devices visible. 2 group devices playing (with EQ bars), 1 solo device stopped/disconnected.

**Capture:** Full-page screenshot at 1400px wide. Collapse config/diagnostics/logs sections.

---

## 2. `screenshot-header.png`

**Used in:** `web-ui.md`

**State:** Top header bar only. Version should show `2.10.x · 2026-03-xx`. Health summary: `2/3 playing · 1 disconnected`.

**Capture:** Crop to header bar only.

---

## 3. `screenshot-device-card-playing.png`

**Used in:** `web-ui.md`

**State:** Single device card (a group member) while actively playing.
- EQ bars animated (4 bars) visible next to player name
- Group badge `🔗 Sendspin BT` visible below name
- Transport row: ◀◀ ▮▮ ▶▶ buttons visible; `api` badge next to MA connection dot
- Track name and artist visible in playback column
- Progress bar with elapsed/total
- Volume slider and audio format

**Capture:** Single card, no hover state.

---

## 4. `screenshot-device-card-hover.png`

**Used in:** `web-ui.md`

**State:** Same card as above, but hovered.
- Hover details visible: MAC, WebSocket URL, adapter MAC, MA host:port, sink name
- Album art popup (120×120) visible above/near track name row
- Action buttons visible: 🔄 Reconnect, 🔗 Re-pair, 🔓 Release
- Delay badge visible in Sync column (e.g. `delay: -600ms`)

**Capture:** Card in hover state (use browser DevTools "Force element state: hover" or manually trigger CSS).

---

## 5. `screenshot-group-controls.png`

**Used in:** `web-ui.md`

**State:** Controls bar above device cards.
- GROUP dropdown showing a group name selected (not "All groups")
- Player count shown
- All checkbox (unchecked)
- VOL slider at ~50%
- 🔈 Mute All, ▮▮ Pause All buttons

**Capture:** Controls bar area only.

---

## 6. `screenshot-config-adapters.png`

**Used in:** `web-ui.md`

**State:** Configuration section expanded, showing Adapters table.
- At least 1 adapter detected (hci0 with MAC and friendly name)
- ↺ Refresh and + Add buttons visible

**Capture:** Config section from top through adapters table.

---

## 7. `screenshot-config-devices.png`

**Used in:** `web-ui.md`

**State:** Devices table in config section.
- 3 device rows visible with: Player Name, MAC, Adapter, Listen Address, Port, Delay ms, Format, × button
- + Add Device, 🔍 Scan, Already Paired buttons visible

**Capture:** Devices table area.

---

## 8. `screenshot-diagnostics.png`

**Used in:** `web-ui.md`

**State:** Diagnostics section expanded.
- System health table with green/red dots
- BT daemon, D-Bus, audio server rows
- hci0 adapter row showing MAC
- Per-device rows (at least 2 green, 1 red)

**Capture:** Full diagnostics section.

---

## 9. `screenshot-logs.png`

**Used in:** `web-ui.md`

**State:** Logs section expanded.
- ~15 lines of log output visible
- Mix of INFO (white), WARNING (amber) lines
- Filter buttons: All / Error / Warning / Info visible

**Capture:** Full logs section.

---

## 10. `screenshot-ha-addon-config.png`

**Used in:** `configuration.md` (EN + RU)

**State:** HA addon Configuration tab, Options section.
- Fields visible: Music Assistant server, Sendspin port, bridge_name, timezone, PULSE_LATENCY_MSEC, PREFER_SBC_CODEC, BT_CHECK_INTERVAL, BT_MAX_RECONNECT_FAILS, auth_enabled
- **NEW:** `ma_api_url` and `ma_api_token` fields visible

**Capture:** HA addon Options panel, full height.

---

## 11. `screenshot-ha-addon-config-bottom.png`

**Used in:** `configuration.md` (EN + RU)

**State:** HA addon config — Bluetooth devices list section.
- At least 2 device entries visible with ✏ Edit and 🗑 Delete buttons
- + Add button visible

**Capture:** Devices section of HA addon config.

---

## 12. `screenshot-ha-addon-device-edit.png`

**Used in:** `configuration.md` (EN + RU)

**State:** Device edit dialog open in HA addon config.
- Fields visible: mac, player_name, adapter, static_delay_ms, listen_host, listen_port, enabled
- **NEW:** `keepalive_silence` and `keepalive_interval` fields visible

**Capture:** Dialog box / modal, full height.

---

## Notes

- Use browser window at 1400×900 for wide shots, 800×600 for card/section crops
- Disable animations for cleaner screenshots (EQ bars can be force-shown via DevTools)
- Use Chrome DevTools → "Capture node screenshot" for precise element captures
- Screenshots go in `docs-site/src/assets/` and are referenced as `/sendspin-bt-bridge/screenshots/<filename>`
- `screenshot-dashboard.png` and `screenshot-config-section.png` and `screenshot-config.png` and `screenshot-device-card.png` appear unused in current docs — can be deleted after retaking replacements
