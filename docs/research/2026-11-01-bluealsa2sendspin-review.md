# What to reuse from `bluealsa2sendspin`

Date: 2026-11-01 · Reviewed commit: `d638cf2` (main, 2026-11-01) ·
Source: <https://github.com/arthurbre/bluealsa2sendspin> · License: Apache-2.0 ·
Size: ~1 030 lines in `src/`, ~900 in `tests/` · Code base: this repo `v3` @ `594a491b`.

Companion to [`2026-11-01-phone-as-source-on-haos.md`](2026-11-01-phone-as-source-on-haos.md),
which establishes that the HAOS path is PulseAudio's `bluez_source`, not BlueALSA. This note is
about the *design* of a working phone-as-source project: what transfers, what does not, and
what it would cost.

## TL;DR

1. **The biggest thing to reuse is not code, it is the role split.** They are a Sendspin
   **source client** of a server that owns the clock; we made the bridge a **Sendspin server**
   for the phone. Our pinned `aiosendspin==10.2.1` already ships the client half
   (`SendspinClient.create_source_capture`, `SourceCapture.feed(pcm, capture_timestamp_us)`,
   `Roles.SOURCE`, `line_sense`), so their whole protocol integration is available to us
   without a dependency change.
2. **Capture-time anchoring and the 500 ms backlog rule are hard-won facts** about that API:
   `feed()` defaults the timestamp to *call time*, drops capture older than
   `MAX_CAPTURE_BACKLOG_US = 500_000`, and `stop()` can refuse during a re-handshake. They
   learned this in commits `f5c0ad6`, `05416d6`, `0de350a`. Any source implementation of ours
   that feeds chunks must respect the same rules.
3. **Event-driven topology beats our polling.** They watch `ObjectManager.InterfacesAdded/
   Removed`; we re-run `wpctl status` every 2 s for the source and the relay. The PulseAudio
   equivalent is `pactl subscribe`.
4. **Their BlueALSA capture is not usable in add-on mode** — it needs to own the A2DP sink
   endpoint, which on HAOS belongs to `hassio-audio`'s `module-bluez5-discover`; taking it
   back would remove every speaker sink. BlueALSA's own README positions it for dedicated
   systems without PulseAudio/PipeWire. It *is* the blueprint for a standalone receiver host.
5. **Four small robustness patterns are worth porting immediately**: stop-with-retries,
   kill-on-timeout (our `PwCapture.stop` can orphan `pw-record`), atomic `0600` identity
   write (ours writes then chmods), and one narrow adapter that seals private `aiosendspin`
   attributes instead of `contextlib.suppress(Exception)` at every call site.
6. **Their test strategy is the model we lack**: `tests/fakes.py` for orchestration races plus
   `tests/test_integration.py` running a real in-process `SendspinServer`, real pairing code,
   PCM round-trip, and reconnect. Our source tests only parse `wpctl` output and check chunk
   arithmetic; nothing exercises the protocol.

## 1. What the project is (and how our shape differs)

```
phone ──A2DP──▶ BlueZ + bluealsad ──org.bluealsa PCM1.Open()──▶ bluealsa2sendspin
                                                                 │ Sendspin source client
                                                                 ▼
                                                    Music Assistant's Sendspin server
                                                                 │
                                                                 ▼
                                                              players
```

Ours (`services/source/bt_source.py`):

```
phone ──A2DP──▶ host PipeWire ──pw-record──▶ BtSendspinSource (a Sendspin *server* on :8940)
                                                         │ push_stream
                                                         ▼
                                              players that connect to us
```

The role difference matters more than the capture backend:

| | bluealsa2sendspin | this repo |
|---|---|---|
| Sendspin role | client, `source` | server, pushes to `group.start_stream()` |
| Who owns the clock/jitter buffer | the server (MA) | we do, in `aiosendspin.server` |
| Player compatibility | any player of that server | only clients that connect to our port |
| Routing to our BT speakers | MA plays the source to our player | our own `relay` (`pw-record \| pw-play`) |
| Setup | pair the phone *and* pair the bridge with MA (8-digit code) | pair the phone; no MA involvement |

Both are legitimate; they answer different product questions. Their model makes the phone a
first-class MA source with zero routing code; ours works without MA and keeps the bridge's
own speakers independent.

## 2. Reusable now, backend-independent

| # | Finding | Their code | Our code | Action |
|---|---|---|---|---|
| 1 | **Event-driven topology**, not polling: `ObjectManager.InterfacesAdded/Removed` triggers a reconcile; no `sleep(2)` hot loop | `bluealsa.py:watch_topology_changes`, `bridge.py:_schedule_topology_sync` | `bt_source.py:find_bluez_input_on_host` + `_watch` poll every 2 s; `relay.py:_watch` polls every 2 s | For PA use `pactl subscribe` (source/card events); for PW use `pw-mon`/`wpctl` events; keep the poll as a fallback. Cuts phone-start latency and CPU |
| 2 | **Timestamp a chunk at its capture start**, not at feed time; `SourceCapture.feed()` otherwise stamps "now" and a stalled backlog looks fresh; >500 ms is dropped | `bridge.py:_feed_loop` passes `now_us - _READ_CHUNK_MS*1000`; commit `f5c0ad6` explains | `bt_source.py:_pump` calls `stream.commit_audio()` with no `play_start_us`; server stamps at commit | Experiment: pass the chunk's start time via `commit_audio(play_start_us=...)` and measure sync jitter. Semantics differ per role — verify before keeping |
| 3 | **Derive chunk size from the actual PCM format** (`frame_bytes * rate * chunk_ms // 1000`) instead of hardcoding stereo/s16/48k | `bridge.py:_ensure_capture_started` | `bt_source.py:chunk_bytes()` hardcodes 48 kHz stereo s16 | Keep the hardcode for the PA/PW path (we force `--rate 48000`), but only after a format check; see #4 |
| 4 | **Decode and validate the codec format up front**, fail with a named error instead of feeding garbage | `bluealsa.py:PcmFormat.decode`, `require_supported`, `UnsupportedPcmFormatError` | none — we assume s16le everywhere | PA can hand `f32le`/24-bit; add a sample-spec check and refuse a format we cannot feed, with a reason surfaced in the source status |
| 5 | **Stop with retries, and release the capture if it will not stop** (5 × 0.2 s, then log and drop) | `bridge.py:_stop_with_retries` | `bt_source.py:PwCapture.stop` terminates, waits 3 s, suppresses `TimeoutError`, then drops the handle — a stuck `pw-record` is orphaned | Add `kill()` after the timeout (the relay already does this in `relay._stop_running`) |
| 6 | **Explicit lock split**: topology/setup vs signal reporting (`_lock`, `_signal_lock`), so a signal update cannot deadlock a reconcile | `bridge.py:SourceBridge` | globals + `_set_state()`; races are implicit | Adopt the two-lock shape in the new capture service; do not add locks to the module-level state |
| 7 | **Deterministic choice when several sources exist**: warn, then take `max(Sequence)` (most recently connected) | `bluealsa.py:_select_source_pcm` | `BLUEZ_INPUT_RE.search` / first match — undefined with two phones | Choose deterministically (latest stream) and log the choice; today two phones are unspecified |
| 8 | **Atomic `0600` identity write**: `os.open(tmp, O_CREAT, 0o600)` then `replace()` | `config.py:_atomic_write_private_json` | `bt_source.py:_load_or_create_identity` writes bytes then `chmod` (brief 0644 window; a crash mid-write truncates the key) | Replace with the atomic pattern; the pairing store writes deserve the same treatment |
| 9 | **Seal private `aiosendspin` surface behind one adapter with a runtime shape check and an actionable error** naming the pin | `bridge.py:_SignalConnection`, `_get_signal_connection`, `_AIOSENDSPIN_PIN` | we call `player.group`, `group.start_stream()`, `player.release_all_role_holds()`, `player.active_roles`, `stream.on_role_join()` under blanket `contextlib.suppress(Exception)` | One `SendspinServerAdapter` that verifies those attributes once and raises a clear error; tests then fail for one reason, not silently |
| 10 | **Test doubles + a real protocol test**: `FakeBlueAlsa`/`FakeSendspinSource` for orchestration races, then a real in-process `SendspinServer` for pairing, start/stop, PCM round-trip, reconnect | `tests/fakes.py`, `tests/test_integration.py` | `tests/unit/services/source/test_bt_source.py` only parses `wpctl` and checks `chunk_bytes()` | Add an integration test that runs `SendspinServer` + a real client, feeds known PCM, and asserts the bytes arrive; it catches aiosendspin drift (we pin exactly) |
| 11 | **Domain language**: distinguish the *read/feed chunk* (their 100 ms slice) from the *server-side playback/jitter buffer* | `CONTEXT.md` | we say "chunk" for both in `bt_source.py` comments | Add to `CONTEXT.md`; the source docs currently blur the two |

## 3. Reusable only with an architecture decision

### 3a. The Sendspin **source client** role

If the phone should appear in Music Assistant as a source (MA 2.10+, `sendspin_source`
provider), their `bridge.py` is the complete recipe, verified against a real server:

1. `SendspinClient(identity, name, [Roles.SOURCE], pairing_store=…,
   source_support=ClientHelloSourceSupport(features=ClientHelloSourceFeatures(line_sense=True)))`.
2. Pair once: enable `static_pairing_code_enabled`, generate an 8-digit code, call
   `open_pairing_window()`, `connect()`, wait (≤5 min) for `noise_psk.category is
   PskCategory.LONG_TERM`. This is a *second* pairing, separate from the phone's Bluetooth
   pairing; the operator enters the code in MA.
3. Run: `connect()` with a 30 s timeout, reconnect with 1→30 s backoff, re-announce the signal
   after every reconnect (`bridge.on_connected`).
4. On the server's `server/command` with `payload.source.command == "start"`:
   `create_source_capture(SupportedAudioFormat(codec=PCM, channels, sample_rate, bit_depth))`,
   `await capture.start()`, then feed whole frames.
5. Report `SignalState.PRESENT` when the phone's PCM exists *and* is running, `ABSENT`
   otherwise; dedupe repeated states; never report before admission.
6. Treat the capture as bound to the connection: on disconnect, cancel the feed task, drop the
   capture, clear the pending start and the last signal; the next start after reconnect creates
   a new capture.

Protocol facts verified in our own `aiosendspin==10.2.1` (not just in their code):

- `SourceCapture.start()` raises unless the clock is synchronized and a server `start` is
  pending; **one server start authorizes one opening** — after the stream ends (stop, server
  stop, role removal, unavailability) a new start is required. Their commit `05416d6` exists
  because an earlier version reopened the capture on PCM replacement by itself.
- `feed()` drops capture older than `MAX_CAPTURE_BACKLOG_US = 500_000`; a 500 ms chunk starts
  exactly at the bound, so they read 100 ms chunks and pass the chunk's start time.
- `stop()` raises `RuntimeError("Connection is busy with an in-band exchange")` during a
  re-handshake; the stream survives, so the call is retriable — hence their 5×0.2 s loop.

The gap to plan around: **`SendspinClient` 10.2.1 has no public `send_source_signal`**. They
reach into `client._admitted_connection.send_source_signal()` and pin `aiosendspin<10.3` so a
shape change cannot slip in silently. For us the options are (a) ask upstream for a public
wrapper before adopting the role, (b) copy their narrow-protocol + shape-check + pin pattern,
or (c) skip signal reporting and rely on start/stop only (worse: MA cannot autostart).

Cost for us: a second Sendspin connection per bridge (source), MA 2.10+ as a hard requirement
for the feature, a new pairing step in the UI, and the disappearance of our own source port and
`relay` for MA-routed playback. Benefit: no server inversion, MA handles playback and sync,
routing to our speakers becomes "play the source to this player" like any other MA source.
This is a product decision, not a refactor; nothing in the current implementation blocks it.

### 3b. BlueALSA as a capture backend

Their D-Bus recipe is clean and could be ported behind our capture protocol:

- `MessageBus(bus_type=SYSTEM, negotiate_unix_fd=True)` — without the fd negotiation the
  `Open()` reply cannot be decoded and the call hangs;
- `org.freedesktop.DBus.ObjectManager.GetManagedObjects` on `/org/bluealsa`, filter
  `Transport == "A2DP-sink"` and `Mode == "source"`, pick `max(Sequence)`;
- `org.bluealsa.PCM1.Open()` returns `(pcm_fd, ctl_fd)`; wrap `pcm_fd` with
  `loop.connect_read_pipe`, close both on teardown;
- watch `org.freedesktop.DBus.Properties.PropertiesChanged` on `Running`;
- decode the 16-bit packed `Format` id and tolerate the `Rate`→`Sampling` rename for
  bluealsad <5.0.

Why it does not fit add-on mode: BlueALSA registers its own A2DP endpoints in BlueZ. On HAOS
the endpoints are already registered by `module-bluez5-discover` (verified live), and
unloading that module to hand the phone to BlueALSA also removes every `bluez_sink` the bridge
plays to. BlueALSA's README frames it as a replacement for PA/PipeWire on dedicated systems,
not a co-tenant. So: port it only for a future "bare receiver host" deployment (a Pi with no
audio server), where it is strictly better than parsing `wpctl` output.

### 3c. Packaging (low priority)

Their systemd installer (dedicated user, XDG state, `EnvironmentFile`, `Restart=on-failure`,
`RestartSec=10`) is a good model *if* we ship the source as a standalone agent. The add-on does
not need it.

## 4. What not to copy

- **BlueALSA as the only capture**, and `Transport`/`Mode` as the only discovery: it makes the
  project host-specific in exactly the way our hardware layer exists to avoid.
- **Single-client assumptions**: one phone, one server, no players, no read model, no UI. Our
  feature has a status surface, a relay and a hardware page; keep them.
- **100 ms feed chunks**: right for their 500 ms backlog bound, worse latency than our 10 ms.
- **The absence of hardware tests**: their BlueALSA/D-Bus code is faked in CI and only
  "exercised against real hardware" manually. If we port it, we need a real-host test matrix,
  not a fake.
- **Silent recovery**: their `except RuntimeError: logger.exception(...)` paths are reasonable
  for a daemon with no UI, but our source status should surface the reason.

## 5. Suggested sequence for this repo (small, independent steps)

1. **Bugs first** (no design needed): `PwCapture.stop()` kills on timeout; remove the
   hardcoded `FC:58:FA:EB:08:6C` from `pairing.py:173`.
2. **Event-driven discovery** for source and relay (`pactl subscribe` on PA, `pw-mon` on PW),
   with the existing 2 s poll as fallback; deterministic multi-source choice.
3. **Capture-time anchoring experiment**: pass the chunk start to `commit_audio(play_start_us=)`
   behind a test that asserts the value, then measure on the stand before shipping.
4. **One `SendspinServerAdapter`** around the private calls (`group.start_stream`,
   `release_all_role_holds`, `on_role_join`) with a shape check, replacing blanket suppresses.
5. **Real-protocol integration test** for the source (server + client + PCM round-trip +
   reconnect), modelled on their `tests/test_integration.py`.
6. **Atomic identity/pairing writes.**
7. **Decision document** for §3a (source client role): answer "MA 2.10+ requirement, second
   pairing, do we keep our own port?" before any code.

## Sources

- Repository (commit `d638cf2`): <https://github.com/arthurbre/bluealsa2sendspin>
  — `src/bluealsa2sendspin/{bluealsa,bridge,cli,config}.py`, `tests/{fakes,test_bridge_orchestration,test_integration}.py`,
  `CONTEXT.md`, `pyproject.toml`.
- Protocol semantics re-verified locally in our pinned environment:
  `.venv/lib/python3.14/site-packages/aiosendspin/client/source.py`
  (`MAX_CAPTURE_BACKLOG_US`, `start()`/`feed()`/`stop()` docstrings) and
  `aiosendspin/server/roles/source/v1.py` (`can_start`, `request_start`).
- BlueALSA positioning: <https://github.com/arkq/bluez-alsa> README ("designed specifically
  for small, low-powered, dedicated audio systems where PulseAudio/PipeWire are not required").
- HAOS topology and the `bluez_source` path: companion note
  [`2026-11-01-phone-as-source-on-haos.md`](2026-11-01-phone-as-source-on-haos.md), §2–§3.
