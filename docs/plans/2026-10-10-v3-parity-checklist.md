# v3 parity checklist (stage 5)

Every area of the legacy web UI (2.77 `templates/index.html` + `static/app.js`)
and where it lives in the v3 SPA. "Gap" rows are what stage 5 leaves open.

Design rules followed throughout: Home Assistant's surfaces, text and status
colours, Roboto, 12 px card radius and settings-row layout; Music Assistant's
accent, button shapes (shadcn-vue "new-york"), lucide icons, rarely used
actions in menus, tap-to-step volume, http(s)-only links, stable layout while
loading. Inside HA ingress the page follows HA's dark mode and accent.

| Legacy area | v3 location | Status |
|---|---|---|
| Header: version, update badge, bug report, docs, theme, language | App header | done |
| Health indicator, restart banner | App header, restart banner | done |
| Onboarding / recovery banners with actions | Dashboard guidance banner (`useGuidanceActions`) | done |
| Config-dirty banner | Settings floating save bar | done |
| Device grid/list, filters (status, adapter, group), group volume/mute/pause | Devices page, group action bar | done |
| Per-device: volume, mute, transport, battery, RSSI | Device card / row | done |
| Per-device menu: reconnect, standby, wake, enable, forget | Card/row menu (now not clipped by the table) | done |
| Per-device settings (name, adapter, port, delay, idle mode, keep-alive, room, volume controller, buffer, lead time) | Drawer → Settings / Timing | done |
| Latency: manual delay, suggestion, click track, microphone calibration | Drawer → Timing | done |
| Recovery actions: pair again, pairing window, unmute sink, power save, release/reclaim | Drawer → Status menu | done |
| Bluetooth scan with adapter choice and compatibility options, pair & add | Scan dialog | done |
| Paired devices list, add to bridge, BT device info | Scan dialog → Paired | done |
| Adapters: names, device class, power/reboot | Settings → Bluetooth; drawer adapter actions | done |
| General: name, time zone, web port, log level | Settings → General | done |
| Music Assistant: sign-in (password, HA with MFA), discovery, server/port, Sendspin test, pairing, volume/mute via MA, live monitor, silent auth, duplicate check | Settings → Music Assistant; MA page | done |
| Audio: PulseAudio buffer with assistant presets, SBC, rescue streams, smooth restart | Settings → Audio | done |
| Reconnect limits, churn isolation, experimental recovery switches, RSSI badge | Settings → Bluetooth | done |
| Home Assistant: mode, MQTT broker/auth/TLS/prefix/client id, detect + test, Mosquitto install, publisher status, integration status, mDNS, announced host/port, area name assist, rooms by adapter | Settings → Home Assistant | done |
| Security: sign-in, password change, session length, lockout, trusted proxies, API tokens | Settings → Security | done |
| Updates: check, channel, auto-update, install | Settings → Updates, update dialog | done |
| Guidance grace periods | Settings → Guidance | done |
| Config export / import / raw JSON | Settings → Backup | done |
| Diagnostics: health, events, recovery timeline (+CSV), bug report, logs (filter, level, download) | Diagnostics tabs | done |
| Telemetry, player processes, webhooks, downloads | Diagnostics → System | done |
| Time-zone live clock preview | — | gap (cosmetic) |
| Guidance visibility toggles (show onboarding / recovery / experimental, reset dismissed) | — | gap: banner has no per-browser hide switches yet |
| Calibration tone for playing through a Music Assistant group (`tone.wav`) | — | gap: per-speaker click track covers the common case |
| HA area lookup with a Home Assistant token (`/ha-integration/areas`) | — | gap: area IDs are typed by hand |
| Config schema v6 (`players[]`) | — | deferred: API v1 does not expose the file layout; see the plan |

Kept for API clients rather than the SPA: `POST /auth/ha-pair` (HA
integration), `GET /bridge`, `GET /devices`, `GET /groups`,
`GET /diagnostics/version`, `GET /music-assistant/debug`.

The settings layout and the device layout are checked against the
`BridgeConfig` / `BluetoothDevice` schemas by unit tests, so a config key added
later without a place on screen fails CI (that is how the announced host/port
keys were found).
