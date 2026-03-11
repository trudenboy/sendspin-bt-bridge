# Reddit Post Draft — r/homeassistant

## Meta

- **Subreddit:** r/homeassistant
- **Flair:** I Made This!
- **Format:** Gallery post (images below) + first comment with detailed write-up
- **Best time:** Tuesday–Thursday, 14:00–17:00 UTC

---

## Title

**I built a Bluetooth Bridge for Music Assistant — multiroom audio from any BT speaker [HA Addon / Docker / LXC]**

---

## Gallery Images (in order)

1. `06-dashboard-viewport.png` — **Main dashboard** showing 5 BT speakers with live playback, volume, sync status, battery levels, and MA group tags
2. `07-multiroom-floorplan.png` — **Multiroom floor plan** — 4 zones, multiple bridges and BT speakers, HA + MA integration
3. `01-dashboard-full.png` — **Full UI overview** — all device cards + group controls bar + Configuration/Diagnostics/Logs sections
4. `03-configuration.png` — **Configuration panel** — BT adapters, device table, MA connection, auth settings
5. `04-diagnostics.png` — **Diagnostics panel** — system health, sink routing, MA sync groups with per-member status

### Image captions

1. "Dashboard with 5 Bluetooth speakers — real-time playback, volume, battery, sync groups"
2. "Multiroom floor plan — multiple bridges across rooms, each controlling BT speakers via A2DP"
3. "Full web UI — device cards, group volume/mute/pause, configuration, diagnostics, logs"
4. "Configuration — BT adapter management, device scan, MA integration, web auth"
5. "Diagnostics — system health check, audio sink routing, MA sync group membership"

---

## First Comment (post this immediately after the gallery)

Hey everyone! I've been working on this project for a few weeks and wanted to share it with the community.

### The problem

Music Assistant is amazing for whole-home audio, but it doesn't natively support Bluetooth speakers. If you have BT speakers around your house (and who doesn't?), they just sit there disconnected from your multiroom setup.

### The solution

**[Sendspin Bluetooth Bridge](https://github.com/trudenboy/sendspin-bt-bridge)** turns any Bluetooth speaker into a full Music Assistant player — with multiroom sync, group controls, and a real-time web UI.

It works by receiving audio from MA via the Sendspin protocol (WebSocket + FLAC) and routing it through PulseAudio/PipeWire to BT speakers over A2DP. Each speaker runs as an isolated subprocess with its own audio context, so routing is always correct from the first sample.

### Key features

- **Multi-device** — connect as many BT speakers as you have USB adapters for (tested with 6 simultaneously)
- **Multiroom sync** — speakers appear as individual players in MA, group them for synchronized playback
- **Auto-reconnect** — exponential backoff with churn isolation; survives speaker power cycles
- **Real-time web UI** — playback controls, volume sliders, battery levels, group management, diagnostics
- **MA API integration** — volume/mute routing through MA keeps all UIs in sync
- **Per-device latency compensation** — `static_delay_ms` setting for A2DP timing differences
- **Battery monitoring** — shows BT battery level per device (BlueZ Battery1)
- **BT device scanning** — discover and add new speakers from the web UI
- **Password auth** — optional web UI protection with PBKDF2-SHA256
- **Update checker** — background version polling with in-UI update badge

### 4 deployment options

| Method | Best for |
|--------|----------|
| **HA Addon** | Home Assistant OS/Supervised — one-click install with Ingress |
| **Docker** | Any Linux box with Bluetooth — `docker compose up` |
| **Proxmox LXC** | Lightweight container on Proxmox VE — full BT passthrough |
| **OpenWrt LXC** | Turris/OpenWrt routers with LXC support |

### Try it now

🎮 **[Live Demo](https://sendspin-bt-bridge.onrender.com)** — fully interactive demo with simulated devices, no installation needed

📖 **[Documentation](https://trudenboy.github.io/sendspin-bt-bridge/)**

🛠 **[GitHub](https://github.com/trudenboy/sendspin-bt-bridge)**

### Technical details (for the curious)

- Python asyncio + Flask/Waitress, ~4500 LOC
- Each BT speaker runs as an isolated subprocess with `PULSE_SINK` env var for correct audio routing
- Parent↔subprocess IPC via JSON lines on stdin/stdout
- SSE for real-time UI updates with 100ms debounce
- D-Bus disconnect detection for instant reconnect
- Tries 4 PulseAudio/PipeWire sink naming patterns for maximum compatibility
- Full REST API (28 endpoints) — automate everything

### Credits

This project started as a fork of [loryanstrant/Sendspin-client](https://github.com/loryanstrant/Sendspin-client) — Loryan's original idea of bridging MA to BT via Sendspin was the spark. I've since rewritten basically everything, but he deserves credit as the founding author.

### What's next

I'm actively developing this — current priorities are improving the documentation site and adding more BT codec options. Feature requests and bug reports are very welcome!

Would love to hear your feedback, especially if you try it with your own BT speakers. What speakers are you using? Any edge cases I should handle?

---

## Notes for posting

- [ ] Ensure Reddit account has recent helpful comments in r/homeassistant (90/10 rule)
- [ ] Post on Tue–Thu, 14:00–17:00 UTC for maximum visibility
- [ ] Post as Gallery with 5 images, then immediately add the first comment
- [ ] Reply to all comments within the first 24 hours
- [ ] If someone asks about latency: "A2DP adds ~150-300ms inherently, the `static_delay_ms` setting lets you compensate per device for multiroom sync"
- [ ] If someone asks about range: "Standard BT range, but you can have multiple bridges (one per room with a Pi) all controlled from MA"
- [ ] Cross-post to r/MusicAssistant if that sub exists and is active
