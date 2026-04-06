# MA Bluetooth Audio Player Provider — Analysis & Architecture Plan

**Date:** 2026-04-06
**Context:** Analysis of Music Assistant PR #3585 (Local Audio Out provider) and feasibility study for implementing Bluetooth audio as an MA player provider with remote bridge orchestration.

---

## 1. Analysis of MA PR #3585 (Local Audio Out)

### What the PR does

New `local_audio` player provider **inside the MA server process** (911 LOC, 11 files):

- Enumerates local soundcards via PortAudio/sounddevice
- Registers each as a Sendspin player using `PlayerType.PLAYER`
- Uses `BridgePlayerRole` from the Sendspin provider for audio streaming
- Audio flow: `Sendspin PushStream → BridgePlayerRole.on_audio_chunk → asyncio.Queue → sounddevice.RawOutputStream → physical soundcard`
- Volume: Hardware (CoreAudio/ALSA amixer) or Software (PCM numpy scaling) or Disabled
- Protocol linking via UUID identifiers for automatic Sendspin association
- Dependency: `depends_on: sendspin` in manifest

### Architecture

```
MA Server process
└── LocalAudioProvider (PlayerProvider)
    └── LocalAudioBridgeManager
        ├── SendspinLocalAudioBridge (Device A) ──► sounddevice ──► hw
        └── SendspinLocalAudioBridge (Device B) ──► sounddevice ──► hw
```

### Key files

| File | LOC | Purpose |
|------|-----|---------|
| `__init__.py` | 61 | Provider entry, config entries, setup |
| `provider.py` | 31 | Thin shell, delegates to bridge manager |
| `sendspin_bridge.py` | 391 | Bridge manager + per-device bridge impl |
| `player.py` | 156 | LocalAudioPlayer with hw/sw volume |
| `coreaudio_volume.py` | 157 | macOS CoreAudio ctypes bindings |
| `constants.py` | 16 | UUID namespace, buffer size, config keys |
| `manifest.json` | 13 | Provider metadata, depends_on: sendspin |

---

## 2. Assessment: Can we use the same approach for our BT bridge?

**Direct reuse: No. But the pattern is the blueprint.**

| Aspect | `local_audio` (PR #3585) | Our bridge |
|--------|--------------------------|------------|
| Where it runs | **Inside** MA server process | **Separate machine** (Pi, LXC, HA addon) |
| Sendspin API | Server-side (`register_external_player`) | Client-side (WebSocket to MA) |
| Device access | Local soundcards via PortAudio | Bluetooth via bluetoothctl, D-Bus (privileged) |
| Audio path | BridgePlayerRole → Queue → sounddevice | MA → Sendspin WS → subprocess → PULSE_SINK → BT |

The `local_audio` provider can use `BridgePlayerRole` because it's **in the same process** as the Sendspin server. Our bridge is an **external client** over the network.

Writing an MA player provider that proxies to our bridge would duplicate what the Sendspin protocol already does — unless we make the provider an **orchestrator** (see Section 4).

---

## 3. Assessment: Does local_audio make our local audio backend unnecessary?

**Priority significantly reduced, but not eliminated.**

### Where MA's `local_audio` fully covers the need:
- **HA addon** (most common deployment) — USB DAC or HDMI on same machine as MA
- Any machine where MA server coexists with physical audio outputs

### Where our local audio backend is still needed:
1. **Standalone Pi without MA** — bridge as self-contained audio receiver with USB DAC
2. **Remote machines** — Pi in another room with USB DAC, connected to MA over network
3. **Container-specific routing** — our `PULSE_SINK` + subprocess isolation
4. **Multi-bridge federation** (ROADMAP v3.3) — multi-room via multiple bridges

### Recommendation:
Bluetooth remains our primary value proposition — MA natively cannot handle BT speakers. Local audio backends (ROADMAP_V3 Phase 1) should be **deferred** until standalone deployment demand justifies it. Focus v3.x on:
1. `BluetoothA2DPBackend` (wrap current BT as backend contract)
2. Backend ABC + config schema v2
3. Leave `LocalSinkBackend` / `ALSADirectBackend` as Phase 1.5

---

## 4. Hybrid Architecture: `bluetooth_audio` MA Player Provider + Bridge Orchestrator

### Core concept

A single MA player provider that handles **both** local Bluetooth (like `local_audio`) **and** orchestrates remote standalone bridges as managed agents.

```
┌─ MA Server ──────────────────────────────────────────────────────┐
│                                                                  │
│  BluetoothAudioProvider                                          │
│  ├── LocalBtManager                                              │
│  │   ├── hci0: ENEBY20 ──► BridgePlayerRole ──► Sendspin        │
│  │   └── hci1: Yandex  ──► BridgePlayerRole ──► Sendspin        │
│  │                                                               │
│  ├── BridgeDiscovery (mDNS listener: _sendspin-bridge._tcp)     │
│  │   ├── Pi-kitchen (192.168.10.50:8080)                         │
│  │   │   └── JBL Flip ─── Sendspin WS ──────────────────────┐   │
│  │   └── Pi-bedroom (192.168.10.51:8080)                     │   │
│  │       └── Sony XM4 ─── Sendspin WS ──────────────────┐   │   │
│  │                                                       │   │   │
│  └── PlayerRegistry                                      │   │   │
│      ├── bt_eneby20      (local, PlayerType.PLAYER)      │   │   │
│      ├── bt_yandex       (local, PlayerType.PLAYER)      │   │   │
│      ├── bt_jbl_flip     (remote:pi-kitchen, PLAYER) ◄───┘   │   │
│      └── bt_sony_xm4     (remote:pi-bedroom, PLAYER) ◄───────┘   │
│                                                                  │
│  MA Sync Groups:                                                 │
│  ├── "Kitchen Party" = [bt_eneby20, bt_jbl_flip]                 │
│  └── "Everywhere"    = [all four devices]                        │
└──────────────────────────────────────────────────────────────────┘
```

### Mode 1: Local Bluetooth

For BT adapters on the **same machine** as MA. Provider directly manages `bluetoothctl`, discovers PA sinks, receives audio via `BridgePlayerRole`.

### Mode 2: Remote Bridge Orchestrator

Provider **discovers standalone bridges** via mDNS (`_sendspin-bridge._tcp.local`) and **proxies** their devices as MA players. Bridge becomes a thin agent; provider is the single control plane.

---

## 5. What gets eliminated

### Current bridge complexity (~54K LOC total)

| Layer | LOC | % |
|-------|-----|---|
| Subprocess management | 1,377 | 2.5% |
| IPC protocol | 459 | 0.8% |
| Bluetooth manager | 706 | 1.3% |
| Web routes (62 endpoints) | 8,344 | 15.3% |
| Static frontend (JS/CSS) | 21,659 | 39.7% |
| Config layer | 1,787 | 3.3% |
| MA integration (WS monitor + client) | 1,919 | 3.5% |
| Diagnostics/guidance | 4,279 | 7.9% |
| State management + SSE | 672 | 1.2% |
| Supporting services | 13,284 | 24.4% |

### What disappears with provider approach (~37K LOC, ~68%)

| Component | LOC removed | Why |
|-----------|-------------|-----|
| Web API + routes | 8,344 | MA Dashboard is the unified UI |
| Vue/JS frontend | 21,659 | Replaced by MA frontend |
| SSE + state sync | 672 | MA player state machine |
| IPC protocol | 459 | No subprocess isolation needed for in-process |
| MA monitor WebSocket | 1,139 | We're **inside** MA — direct API access |
| MA client/discovery | 444 | `self.mass` available directly |
| Config persistence | 1,787 | MA's `ProviderConfig` + `ConfigEntry` |
| Auth layer | 772 | MA handles authentication |
| Update checker | 447 | MA addon update mechanism |
| Diagnostics/guidance | 4,279 | Provider has direct access to all state |

### What remains in the provider (~5K LOC)

| Component | LOC | Role |
|-----------|-----|------|
| BT manager (core) | ~500 | bluetoothctl pairing/connect/reconnect |
| PA sink discovery | ~200 | `configure_bluetooth_audio()` — 4 sink patterns |
| Sendspin bridge | ~400 | Per `SendspinLocalAudioBridge` pattern from PR #3585 |
| Player model | ~200 | `BluetoothPlayer(Player)` with BT-specific features |
| Provider shell | ~100 | `BluetoothProvider(PlayerProvider)` — discover/unload |
| D-Bus disconnect | ~150 | Instant disconnect detection |
| Reconnect logic | ~200 | Exponential backoff + churn isolation |
| Bridge discovery | ~200 | mDNS listener + REST client for remote bridges |

---

## 6. Standalone bridge becomes thin agent (~2K LOC)

```
standalone-bridge/
├── bt_manager.py        # bluetoothctl + D-Bus reconnect (~500)
├── sendspin_daemon.py   # Sendspin client subprocess (~400)
├── pa_routing.py        # PA sink discovery (~200)
├── bridge_api.py        # Minimal REST for provider (~300)
├── mdns_advertise.py    # _sendspin-bridge._tcp (~100)
└── main.py              # Entrypoint (~100)
```

### Bridge Management API (provider → bridge)

```
GET  /api/devices          → list BT devices with status
POST /api/bt/scan          → start BT scan
POST /api/bt/pair          → pair device
POST /api/bt/connect       → connect device
POST /api/bt/disconnect    → disconnect device
GET  /api/health           → bridge health status
WS   /api/events           → real-time events (connect/disconnect/error)
```

### mDNS advertisement (bridge → provider)

```
_sendspin-bridge._tcp.local
    → name: "Living Room Bridge"
    → version: "3.3.0"
    → player_count: 3
    → api_port: 8080
    → api_version: 1
```

---

## 7. Key benefits

### 7.1 Unified UI for all BT speakers
```
MA Dashboard
├── 🔊 ENEBY20 (local, hci0)            ← Mode 1
├── 🔊 Yandex Mini (local, hci1)        ← Mode 1
├── 🔊 Kitchen JBL (bridge: Pi-kitchen)  ← Mode 2
├── 🔊 Bedroom Sony (bridge: Pi-bed)     ← Mode 2
└── 🔊 Chromecast Living Room            ← native MA
```

### 7.2 Audio path simplification: 3 hops → 1 (local)
```
Before:  MA → Sendspin WS → subprocess stdin → PULSE_SINK → BT
After:   MA → BridgePlayerRole.on_audio_chunk → PULSE_SINK → BT
```

### 7.3 Volume/playback: 2 round-trips → direct call
```
Before:  MA UI → MA API → Sendspin WS → Bridge → IPC → subprocess → pactl
After:   MA UI → Player.volume_set() → pactl/amixer
```

### 7.4 Status: eventual consistency → single source of truth
Before: status lives in 3 places (subprocess DeviceStatus, parent state.py, MA player state). After: only `Player.update_state()`.

### 7.5 Config: custom format → standard MA
```python
async def get_config_entries(mass, instance_id, action, values):
    return (
        ConfigEntry(key="bt_adapters", type=ConfigEntryType.STRING, ...),
        ConfigEntry(key="bridge_discovery", type=ConfigEntryType.BOOLEAN, default_value=True, ...),
        ConfigEntry(key="prefer_sbc_codec", type=ConfigEntryType.BOOLEAN, default_value=False, ...),
    )
```

### 7.6 Multi-room across local + remote — free
MA sync groups already handle Sendspin player grouping. BT speaker on local hci0 + BT speaker on remote Pi → one group, synced via Sendspin.

---

## 8. MA Provider API surface (from codebase analysis)

### PlayerProvider base class methods

| Method | Purpose |
|--------|---------|
| `handle_async_init()` | Async initialization |
| `loaded_in_mass()` | Called after full load — trigger discovery |
| `unload(is_removed)` | Cleanup/deregistration |
| `discover_players()` | Enumerate devices |
| `remove_player(player_id)` | Remove a player |
| `create_group_player(name, members)` | Create group |
| `on_player_enabled(player_id)` | Player enabled callback |
| `on_player_disabled(player_id)` | Player disabled callback |

### Sendspin external player registration pattern

Used by Chromecast, AirPlay, and local_audio providers:

1. **Pre-register identifiers**: `sendspin_provider.register_bridge_identifiers(client_id, {IdentifierType.UUID: uuid})`
2. **Register external player**: `sendspin_server.register_external_player(hello_payload, on_stream_start=callback)`
3. **Get BridgePlayerRole**: `client.roles_by_family("player")[0]` → set callbacks for audio chunks
4. **MA creates SendspinPlayer**: Automatically via `ClientAddedEvent`

### Cross-protocol linking

MA supports `CONF_LINKED_PROTOCOL_IDS` and `active_output_protocol` for linking protocol players to native players. BT players can be linked across providers.

---

## 9. Risks and mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Privileged BT access in MA container** | 🔴 High | MA addon manifest: `privileged: true`, D-Bus mount, NET_ADMIN cap |
| **Multi-adapter support (hci0/hci1)** | 🟡 Medium | Per-device ConfigEntry for adapter assignment |
| **Reconnect resilience** | 🟡 Medium | Port battle-tested 400+ LOC reconnect logic from current bridge |
| **PA sink race on BT reconnect** | 🟡 Medium | `module-rescue-streams` correction — simpler in-process |
| **PipeWire compatibility** | 🟡 Medium | Dual sink pattern matching (already implemented) |
| **MA release cycle dependency** | 🟠 Political | Bug in BT → wait for MA release. Mitigate with own addon/HACS |
| **Bridge offline** | 🟢 Low | Provider marks player.available=False |
| **Bridge API versioning** | 🟢 Low | Semver in mDNS TXT `api_version=1`. Provider supports N and N-1 |
| **Duplicate device across bridges** | 🟢 Low | Provider deduplicates by MAC. First registrant owns |

---

## 10. Implementation roadmap

### Phase 0: `bluetooth_audio` provider — local mode only
- bluetoothctl wrapper (pair/connect/reconnect/D-Bus disconnect)
- PA sink discovery (4 patterns: PipeWire + PulseAudio variants)
- BridgePlayerRole per device (pattern from PR #3585)
- ConfigEntry UI (adapters, codec preference)
- manifest.json with `depends_on: sendspin`
- **Deliverable:** MA addon with local BT speaker support

### Phase 1: Bridge agent protocol
- Define minimal REST API spec for bridge management
- mDNS advertisement (`_sendspin-bridge._tcp.local`)
- Refactor standalone bridge → thin agent (~2K LOC)
- **Deliverable:** Bridge agent package (PyPI or Docker)

### Phase 2: Remote bridge orchestration
- mDNS discovery in provider
- `RemoteBluetoothPlayer` proxy class
- Bridge management commands via REST
- Real-time event streaming via WS
- **Deliverable:** Unified local + remote BT in MA Dashboard

### Phase 3: Advanced features
- Cross-bridge sync groups
- Auto-delay calibration
- Bridge health monitoring & diagnostics
- Adapter area mapping (via HA device/area registry)

---

## 11. Shared library strategy

Core BT management (~1.5K LOC) can be extracted to a shared PyPI package used by both the MA provider and the standalone bridge agent:

```
sendspin-bt-core/
├── bt_manager.py        # bluetoothctl wrapper
├── pa_sink.py           # PA/PW sink discovery
├── dbus_monitor.py      # D-Bus disconnect detection
├── reconnect.py         # Exponential backoff + churn
└── types.py             # Shared data models
```

This avoids code duplication and ensures both deployments share battle-tested BT logic.

---

## 12. Conclusion

The `bluetooth_audio` MA player provider with bridge orchestration is the **architecturally optimal path**:

- **Eliminates ~68% of current bridge codebase** (37K of 54K LOC)
- **Unified user experience** — all BT speakers in MA Dashboard
- **Multi-room across local + remote** — free via MA sync groups
- **Standalone bridge preserved** — but as thin managed agent
- **Pattern proven** — PR #3585 (`local_audio`) is the exact blueprint
- **Incremental delivery** — Phase 0 (local only) is independently valuable
