# Group sync tuning — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator aligns every adjustable Sendspin player of a Music Assistant sync group from the bridge's web UI — by ear (manual), from a phone-microphone measurement with review (semi-automatic) or in one go with verification and undo (automatic) — and keeps named delay profiles.

**Architecture:** A new portable package `sendspin_sync` (click track, estimator, planner, session state machine, ports) knows nothing about the bridge. `sendspin_bridge.sync_tuning` implements the ports over Music Assistant's WebSocket API, `config.json` and a public FastAPI click-track route; `application/sync_tuning.py` + `api/routers/sync.py` expose `/api/v1/sync/*` and push `sync` events on the existing event stream. The Vue SPA adds a `/sync/:groupId` screen (group, profiles, manual tuning, measurement wizard) with an AudioWorklet capture.

**Tech Stack:** Python 3.13/3.14 (stdlib DSP, optional numpy never shipped), FastAPI, Pydantic v2, pytest; `music-assistant-client` 1.4.3 / `websockets` for Music Assistant; Vue 3 + TS + Pinia + vue-i18n, vitest, AudioWorklet.

**Spec:** [`docs/plans/2026-10-15-group-sync-tuning-design.md`](2026-10-15-group-sync-tuning-design.md) (review-fixed 2026-10-15). Stage 0's decision note is appended to the spec and read by every later task.

## Global Constraints

Copied from the spec; every task's requirements include them.

* Delays: "Delays are 0–5000 ms; the client reports `round(output_delay_ms)` as an integer in `client/state`." A larger delay makes a player sound **earlier**.
* Source of truth: "In Music Assistant: the per-player config value `sendspin_static_delay` is the source of truth for every Sendspin player." Only members whose `config/players/get_entries` contains `sendspin_static_delay` are `adjustable`.
* Signal: "WAV, 48 kHz, 16-bit, stereo (identical channels), 180 s, −12 dBFS peak." "Chirp: linear 2→6 kHz, 8 ms, Hann window." "Period **P = 2 s**. Every click is a **pair** of chirps; … `gap = 40 + 20·k ms`, `k = 0…4`." "The track starts with 1 s of silence."
* Capture: "6 s per member at the speaker, 12 s from the listening position." "decimate by an integer factor to the lowest rate ≥ 16 kHz"; upload "int16 little-endian binary (`application/octet-stream`)" with `start_time_s`, `sample_rate`, `member` as query parameters.
* Estimation: "Accept when at least 2 clicks are found, their phase spread is ≤ 3 ms and the peak SNR is ≥ 10 dB; otherwise `not_heard` or `unstable` with the reason." Pure Python is the production path.
* Planning: "`d_i' = d_i + (e_i − min e)`"; "Normalise only when every adjustable member was measured"; "Round to 1 ms, clamp to 0–5000; a clamped row is marked and the predicted residual reported"; "if the residual spread is above 5 ms, applies one refinement (at most two rounds in total)"; "proposal warns above 2000 ms".
* Session: "Only one session per group." "writes delays live (debounced 300 ms)." "automatic mode keeps an undo for 30 s." "a 15-minute inactivity timeout". Restore "never touches the delays".
* Core boundary: "A test fails the build if it imports anything from `sendspin_bridge`." `sendspin_sync` is listed in `[tool.hatch.build.targets.wheel] packages`.
* Persistence: `SYNC_PROFILES` in `DEFAULT_CONFIG`, `config/schema.json` and the `web_ui_only_keys` tuple of `scripts/translate_ha_config.py`; "Recordings are kept in memory for the session only, never written to disk."
* Security: "`GET /api/v1/sync/signal/{token}.wav` is public … the token is 32 random bytes, compared in constant time, and a wrong token is a 404." Everything else under `/api/v1/sync/*` uses `require_principal` and CSRF. Permission errors surface as `ma_forbidden` "with the proposal still shown".
* Events: "`sync` is a new event type: it joins `ALL_TYPES` and `DEFAULT_TYPES` in the event hub … and the `EventEnvelope['type']` union of the SPA."
* UX: "Keyboard accessible; step status in an aria-live region; en and ru."
* Gates before every commit that touches them: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`; `uv run ruff check . && uv run ruff format --check .`; `cd ui && npm test && npm run build`; after API model changes `uv run python scripts/export_openapi.py` then `cd ui && npm run gen:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md` (rules R5–R9 in `CLAUDE.md`). TDD per `CLAUDE.md`: red before green, never edit a test to make it pass.
* Do not touch the bridge on port 8080 or anything on 8095/8097 while developing; the live check (stage 5) uses the test stand only.

## Review Focus

1. **A group member that cannot be adjusted** (Chromecast, or a Sendspin client that does not declare `set_output_delay`) — it is listed as "cannot be adjusted", the planner leaves it out, `PUT …/delays` for it answers 409 `not_adjustable`, and the proposal still aligns the others. (Tasks 1.4, 2.3, 2.5)
2. **A token without `CONFIG_PLAYERS_WRITE`** — apply fails with 403 `ma_forbidden`, the session stays in `proposed` with the proposal intact, nothing is half-applied, and the UI shows the values to copy. (Tasks 2.3, 2.5, 4.3)
3. **Garbage recordings** — silence, 0.3 s of audio, a single click, a 10 MB body, an odd byte count, a 7 kHz sample rate: the estimator answers `not_heard`/`unstable` and the API answers 400 `recording_invalid`; nothing raises 500 and the session continues. (Tasks 1.3, 2.5)
4. **Bridge restart mid-session** — the persisted snapshot stops the signal and unmutes what the session muted on the next boot, the old session id answers 404 `session_expired`, and the group is not left muted when Music Assistant is unreachable at boot (restore keeps retrying). (Task 2.4)
5. **Lags at the cycle edge and delays at the clamp** — a member 4.9 s behind and one 0.2 s ahead unwrap correctly; a proposal that would exceed 5000 ms is clamped, marked, and its predicted residual reported; a proposal above 2000 ms carries the warning. (Tasks 1.3, 1.4)

---

## File map

| File | Change |
|---|---|
| `pyproject.toml` | add `src/sendspin_sync` to hatch `packages`, `sendspin_sync` to `known-first-party` |
| `src/sendspin_sync/__init__.py`, `models.py`, `click_track.py`, `estimator.py`, `planner.py`, `ports.py`, `session.py` | **new** portable core |
| `tests/unit/sendspin_sync/test_boundary.py`, `test_models.py`, `test_click_track.py`, `test_estimator.py`, `test_planner.py`, `test_session.py`, `tests/unit/sendspin_sync/synth.py` | **new** core tests + synthetic recordings |
| `tests/integration/test_packaging.py` | hatch list pins `sendspin_sync` |
| `src/sendspin_bridge/config/__init__.py`, `config/schema.json`, `scripts/translate_ha_config.py` | `SYNC_PROFILES` |
| `src/sendspin_bridge/sync_tuning/__init__.py`, `profiles.py`, `click_track_host.py`, `ma_api.py`, `ma_group_control.py`, `session_store.py` | **new** bridge adapters |
| `src/sendspin_bridge/api/events.py` | `sync` event type |
| `src/sendspin_bridge/application/models/sync.py`, `application/sync_tuning.py`, `api/routers/sync.py`, `api/app.py` | **new** use cases + HTTP |
| `scripts/export_sync_contract.py`, `docs/contracts/sync-contract-v1.json` | **new** contract export (`--check`) |
| `ui/src/api/sync.ts`, `ui/src/api/types.ts`, `ui/src/stores/sync.ts`, `ui/src/router/index.ts`, `ui/src/views/SyncView.vue`, `ui/src/components/sync/*.vue`, `ui/src/sync/*.ts`, `ui/src/i18n/{en,ru}.json`, `ui/src/components/ma/MaGroupList.vue`, `ui/src/components/devices/DeviceTimingPanel.vue` | **new/modified** SPA |
| `ui/tests/unit/sync/*.test.ts`, `ui/tests/component/sync/*.test.ts` | **new** SPA tests |
| `docs-site/src/content/docs/web-ui.mdx`, `CHANGELOG.md`, `VERSION` | docs + release |

Conventions used below: Python tests live under `tests/unit/...` and use the fixtures in `tests/conftest.py` (`api_client`, `tmp_config`, `bridge_loop`) and `tests/support/api_client.py` (`make_client`). Use cases raise `UseCaseError(status, code, detail)` from `sendspin_bridge.application.errors`; routers return Pydantic models and attach `dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES`. Loop access from worker threads: `run_on_loop(coro, timeout=…, description=…)` / `submit_on_loop(coro, description=…)` from `sendspin_bridge.application.runtime`.

---

## Stage 0 — Spike on Music Assistant dev (throwaway)

### Task 0.1: Spike experiments and decision note

**Files:**
- Create (not committed, `.gitignore`d): `scratch/sync_spike/track.py`, `scratch/sync_spike/ma.py`, `scratch/sync_spike/estimate.py`
- Modify: `docs/plans/2026-10-15-group-sync-tuning-design.md` (append "Stage 0 decision note")

**Interfaces:**
- Consumes: Music Assistant dev on the HAOS stand (`http://192.168.10.10:8095`, token from the bridge config `MA_API_TOKEN`), a sync group with one bridge player and the web kiosk player; `music_assistant_client.MusicAssistantClient(url, None, token=…)` with `send_command(cmd, **args)` / `send_command_no_wait`.
- Produces: the decision note with the **signal/solo choice (a, b or c)**, the **token/scope finding**, the **mid-announcement mute finding**, and the **estimator accuracy table**. Tasks 2.3 and 4.1 branch on it.

- [ ] **Step 1: Generate the click track** — `scratch/sync_spike/track.py` renders 180 s with the spec's chirp pairs (copy the maths from Task 1.2 once written, or write it inline for the spike) to `scratch/sync_spike/track.wav`, and serves it with `python -m http.server 8099 --directory scratch/sync_spike` from the dev machine (the bridge's 8080 stays untouched).

- [ ] **Step 2: E1 — live config write.** `scratch/sync_spike/ma.py`:

```python
import asyncio, sys, time
from music_assistant_client import MusicAssistantClient

URL, TOKEN = sys.argv[1], sys.argv[2]

async def main():
    async with MusicAssistantClient(URL, None, token=TOKEN) as c:
        await c.connect()
        players = await c.send_command("players/all", return_protocol_players=True)
        for p in players:
            print(p["player_id"], p["type"], p["provider"], p["name"], p.get("output_protocols"))
        pid = input("bridge player id (client_id): ").strip()
        entries = await c.send_command("config/players/get_entries", player_id=pid)
        print("has delay entry:", any(e["key"] == "sendspin_static_delay" for e in entries))
        print("value:", await c.send_command("config/players/get_value", player_id=pid, key="sendspin_static_delay"))
        t0 = time.monotonic()
        await c.send_command("config/players/save", player_id=pid, values={"sendspin_static_delay": 120})
        print("save took", round(time.monotonic() - t0, 2), "s")

asyncio.run(main())
```

Run: `uv run python scratch/sync_spike/ma.py http://192.168.10.10:8095 "$TOKEN"`. Then `curl -s http://<stand bridge>:8080/api/v1/status | jq '.devices[].audio.static_delay_ms'` must show `120` within 2 s without a reconnect; repeat for the web kiosk player (its id from `players/all`; check whether the kiosk client lists `set_output_delay` — if it has no entry, note "kiosk not adjustable"). Record: whether the save succeeded with the bridge's token (**scope finding**: success → the token's role carries `CONFIG_PLAYERS_WRITE`; an error with code 401/403 → note the exact error payload for Task 2.3's `ma_forbidden` mapping). Restore the delay to its previous value.

- [ ] **Step 3: E2 — option (a), announcement + mid-stream mute.** Extend `ma.py` with a second mode: start the group's queue playing music, then `send_command_no_wait("players/cmd/play_announcement", player_id=GROUP_ID, url="http://<dev-ip>:8099/track.wav", pre_announce=False)`; after the group reports `playback_state == "playing"` (poll `players/get`), call `players/cmd/volume_mute` `{player_id: MEMBER, muted: true}` on one member; listen; after 20 s unmute; then `players/cmd/stop` on the group. Record: (1) does the clip play in sync on both members; (2) does the member go silent while muted (**mid-announcement mute finding**); (3) after stop, does the music resume where it was and is the member's mute state what it was before (expected: MA does not unmute a member muted during the clip — the adapter must); (4) how long the `play_announcement` reply takes (expected: until the clip ends — confirms `send_command_no_wait`); (5) announcement volume: was the clip played at the queue volume or at an announcement level (note the number for Task 2.3's `volume_level` argument).

- [ ] **Step 4: E3 — option (b), only if (a) fails.** Snapshot `player_queues/get` `{queue_id: GROUP_ID}` (`current_index`, `elapsed_time`, `shuffle_enabled`, `repeat_mode`) and `player_queues/items` `{queue_id, limit: 500}`; `player_queues/play_media` `{queue_id, media: "http://…/track.wav", option: "replace"}`; mute as in E2; then restore: `player_queues/play_media` with the saved items' `uri`s (`option: "replace"`), `player_queues/play_index` `{queue_id, index}`, `player_queues/seek` `{queue_id, position}`, `player_queues/shuffle`, `player_queues/repeat`. Record whether the queue came back identical (items, index, position within 2 s) and how long it took with the stand's queue of ≥ 100 items.

- [ ] **Step 5: E4 — option (c), bridge-side solo latency.** With the clip playing, `curl -X POST http://<stand bridge>:8080/api/v1/devices/<id>/mute -d '{"muted": true}'` (the existing `set_device_mute` route — check its exact path in `ui/openapi.json`) and time silence start/stop by ear or with the phone recorder. Record the latency (expected < 100 ms) versus MA's mute (E2).

- [ ] **Step 6: E5 — estimator accuracy on real recordings.** On the phone, open the voice recorder (uncompressed WAV if offered; otherwise the highest-quality setting), play the clip on the group via E2, record 6 s at ~40 cm from member A, then 6 s from member B, with the bridge player's delay at 0 and then at +120 ms (set through E1). Copy the files to `scratch/sync_spike/rec/`. `scratch/sync_spike/estimate.py` is a throwaway copy of the Task 1.3 algorithm (band envelope → candidates → matched filter → pair gap → phase) run on both files: print the lag B−A for the 0 ms and +120 ms runs. Record the two lags and their difference (expected 120 ± 5 ms) and the per-recording spread.

- [ ] **Step 7: Write the decision note.** Append to the spec:

```markdown
## Stage 0 decision note (YYYY-MM-DD)

- Signal + solo: **(a | b | c)** — because … (E2/E3/E4 results, one line each).
- Mid-announcement mute: honoured / not honoured; MA restore leaves it: yes/no.
- Token scopes: bridge token could / could not write player config (error payload: …).
- Web kiosk: adjustable yes/no (entry present / absent).
- Announcement volume observed: …; adapter passes `volume_level` = …
- Estimator: lag(0 ms) = … ms, lag(+120 ms) = … ms, difference = … ms, spread … ms.
- Open issues for later stages: …
```

Branching for later tasks: **(a)** → Task 2.3 implements `AnnouncementSignal` + `MaMuteSolo` (the default code in this plan). **(c)** → Task 2.3 additionally implements `HybridSolo` (bridge members through `set_device_mute`) and wires it instead of `MaMuteSolo`. **(b)** → Task 2.3 implements `QueueSignal` instead of `AnnouncementSignal` (code given in Task 2.3). Task 4.1 reads the mute latency to size the wizard's settle time (`SOLO_SETTLE_MS`, default 800).

- [ ] **Step 8: Commit** only the spec: `git add docs/plans/2026-10-15-group-sync-tuning-design.md && git commit -m "docs: group sync tuning — stage 0 decision note"`. `scratch/` is deleted.

---

## Stage 1 — Core package `sendspin_sync`

### Task 1.1: Package skeleton, models, import boundary, packaging

**Files:**
- Create: `src/sendspin_sync/__init__.py`, `src/sendspin_sync/models.py`, `tests/unit/sendspin_sync/__init__.py`, `tests/unit/sendspin_sync/test_boundary.py`, `tests/unit/sendspin_sync/test_models.py`
- Modify: `pyproject.toml` (hatch `packages`, isort `known-first-party`), `tests/integration/test_packaging.py`

**Interfaces:**
- Produces (used by every later task):

```python
# src/sendspin_sync/models.py
Origin = Literal["bridge", "sendspin", "other"]
MeasurementStatus = Literal["ok", "not_heard", "unstable", "skipped"]
ProfileKind = Literal["device", "position"]

@dataclass(frozen=True)
class Member:
    ma_player_id: str
    name: str
    adjustable: bool
    origin: Origin
    client_id: str | None = None
    delay_ms: int | None = None
    last_measured_at: str | None = None

@dataclass
class Recording:              # int16 little-endian mono PCM
    member: str               # Member.ma_player_id
    start_time_s: float       # capture clock, seconds
    sample_rate: int
    pcm: bytes

@dataclass(frozen=True)
class Measurement:
    member: str
    status: MeasurementStatus
    phase_ms: float | None = None
    confidence: float = 0.0
    clicks: int = 0
    spread_ms: float | None = None
    reason: str = ""

@dataclass(frozen=True)
class ProposalRow:
    member: str; before_ms: int; after_ms: int; delta_ms: int; clamped: bool

@dataclass(frozen=True)
class Proposal:
    reference: str
    rows: tuple[ProposalRow, ...]
    predicted_spread_ms: float
    unverified: tuple[str, ...]
    warnings: tuple[str, ...]

@dataclass
class Profile:
    id: str; name: str; group_id: str; kind: ProfileKind
    delays: dict[str, int]; created_at: str; notes: str = ""

@dataclass
class Snapshot:
    group_id: str
    delays: dict[str, int]
    volumes: dict[str, int | None]
    muted: dict[str, bool]
    playing: bool
    restore_token: dict[str, Any] = field(default_factory=dict)

def to_dict(obj) -> dict          # dataclasses.asdict with tuples → lists
def profile_from_dict(d) -> Profile
def snapshot_from_dict(d) -> Snapshot
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/sendspin_sync/test_boundary.py
"""The core must stay portable: nothing from the bridge may be imported."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[3] / "src" / "sendspin_sync"


def test_core_never_imports_the_bridge():
    offenders = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.startswith("sendspin_bridge")]
    assert offenders == []


def test_package_imports():
    import sendspin_sync

    assert sendspin_sync.__all__
```

```python
# tests/unit/sendspin_sync/test_models.py
from sendspin_sync.models import Profile, Snapshot, profile_from_dict, snapshot_from_dict, to_dict


def test_profile_round_trips_through_json_shape():
    p = Profile(id="p1", name="Evening", group_id="g", kind="device", delays={"a": 10, "b": 0}, created_at="2026-10-15T10:00:00Z")
    assert profile_from_dict(to_dict(p)) == p


def test_snapshot_round_trips_and_keeps_opaque_token():
    s = Snapshot(group_id="g", delays={"a": 1}, volumes={"a": 40}, muted={"a": False}, playing=True, restore_token={"announcing": True})
    d = to_dict(s)
    assert d["restore_token"] == {"announcing": True}
    assert snapshot_from_dict(d) == s
```

Add to `tests/integration/test_packaging.py`:

```python
def test_wheel_ships_the_sync_core():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/sendspin_bridge", "src/sendspin_sync"]
```

- [ ] **Step 2: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sendspin_sync tests/integration/test_packaging.py` — Expected: FAIL (`ModuleNotFoundError: sendspin_sync`, packaging assertion).

- [ ] **Step 3: Implement.** `pyproject.toml`: `packages = ["src/sendspin_bridge", "src/sendspin_sync"]`, `known-first-party = ["sendspin_bridge", "sendspin_sync"]`. `src/sendspin_sync/__init__.py`:

```python
"""Group sync tuning core: click track, estimator, planner and session (bridge-agnostic)."""

from sendspin_sync.models import Measurement, Member, Profile, Proposal, ProposalRow, Recording, Snapshot

__all__ = ["Measurement", "Member", "Profile", "Proposal", "ProposalRow", "Recording", "Snapshot"]
```

`models.py` as in Interfaces, plus:

```python
def to_dict(obj: Any) -> dict[str, Any]:
    return json.loads(json.dumps(dataclasses.asdict(obj)))  # tuples → lists, bytes refused on purpose

def profile_from_dict(d: Mapping[str, Any]) -> Profile:
    return Profile(id=str(d["id"]), name=str(d["name"]), group_id=str(d["group_id"]), kind=d.get("kind", "device"),
                   delays={str(k): int(v) for k, v in dict(d.get("delays") or {}).items()},
                   created_at=str(d.get("created_at") or ""), notes=str(d.get("notes") or ""))

def snapshot_from_dict(d: Mapping[str, Any]) -> Snapshot:
    return Snapshot(group_id=str(d["group_id"]), delays={str(k): int(v) for k, v in dict(d.get("delays") or {}).items()},
                    volumes={str(k): (None if v is None else int(v)) for k, v in dict(d.get("volumes") or {}).items()},
                    muted={str(k): bool(v) for k, v in dict(d.get("muted") or {}).items()},
                    playing=bool(d.get("playing")), restore_token=dict(d.get("restore_token") or {}))
```

(`Recording.pcm` is bytes and is never serialised — `to_dict` is not used on it.) Re-run `uv sync --frozen --extra dev` so the editable install picks up the new package.

- [ ] **Step 4: Run** the same command — Expected: PASS. Gates: `uv run ruff check . && uv run ruff format --check .`, `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`.

- [ ] **Step 5: Commit** `git add pyproject.toml src/sendspin_sync tests/unit/sendspin_sync tests/integration/test_packaging.py && git commit -m "feat(sync): portable sendspin_sync package with models and import boundary"`.

---

### Task 1.2: Click track

**Files:**
- Create: `src/sendspin_sync/click_track.py`, `tests/unit/sendspin_sync/test_click_track.py`

**Interfaces:**
- Produces:

```python
SAMPLE_RATE = 48000; DURATION_S = 180; LEAD_IN_S = 1.0; PERIOD_S = 2.0; CYCLE_CLICKS = 5
CYCLE_S = PERIOD_S * CYCLE_CLICKS           # 10 s
CHIRP_MS = 8; CHIRP_F0 = 2000.0; CHIRP_F1 = 6000.0
GAP_BASE_MS = 40; GAP_STEP_MS = 20; PEAK = 0.25   # −12 dBFS

def gap_ms(k: int) -> int                          # 40 + 20·k
def chirp(sample_rate: int) -> list[float]         # Hann-windowed linear chirp, peak 1.0
def click_times(duration_s: float = DURATION_S) -> list[tuple[float, int]]  # (t_first_chirp_s, k)
def render_pcm(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes  # stereo int16
def render_wav(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes
```

- [ ] **Step 1: Failing tests**

```python
import struct, wave, io
from sendspin_sync import click_track as ct


def _mono(pcm: bytes) -> list[int]:
    return [l for l, _r in struct.iter_unpack("<hh", pcm)]


def test_click_schedule_follows_period_and_cycle():
    times = ct.click_times(21.0)
    assert times[:6] == [(1.0, 0), (3.0, 1), (5.0, 2), (7.0, 3), (9.0, 4), (11.0, 0)]
    assert ct.gap_ms(4) == 120 and ct.CYCLE_S == 10.0


def test_track_is_silent_for_one_second_then_pairs_of_chirps_at_minus_12_dbfs():
    pcm = ct.render_pcm(duration_s=4, sample_rate=48000)
    mono = _mono(pcm)
    assert max(abs(v) for v in mono[:48000]) == 0
    first = mono[48000:48000 + 384]                 # 8 ms chirp
    assert 7500 <= max(abs(v) for v in first) <= 8300   # 0.25 × 32767 ≈ 8192
    second_start = 48000 + round(0.040 * 48000)    # k=0 → 40 ms gap
    second = mono[second_start:second_start + 384]
    assert max(abs(v) for v in second) >= 7500
    gap = mono[48000 + 384:second_start]
    assert max(abs(v) for v in gap) == 0
    # k=1 click at 3 s has a 60 ms gap
    third_pair = 3 * 48000 + round(0.060 * 48000)
    assert max(abs(v) for v in mono[third_pair:third_pair + 384]) >= 7500


def test_channels_are_identical_and_wav_header_is_right():
    wav = ct.render_wav(duration_s=2)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, 48000, 96000)
    assert all(l == r for l, r in struct.iter_unpack("<hh", ct.render_pcm(duration_s=2)))


def test_render_is_deterministic():
    assert ct.render_pcm(duration_s=3) == ct.render_pcm(duration_s=3)
```

- [ ] **Step 2: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sendspin_sync/test_click_track.py` — Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

```python
"""The click track: chirp pairs every 2 s whose gap encodes the click index in a 10 s cycle."""
from __future__ import annotations
import io, math, struct, wave

SAMPLE_RATE = 48000; DURATION_S = 180; LEAD_IN_S = 1.0; PERIOD_S = 2.0; CYCLE_CLICKS = 5
CYCLE_S = PERIOD_S * CYCLE_CLICKS
CHIRP_MS = 8; CHIRP_F0 = 2000.0; CHIRP_F1 = 6000.0
GAP_BASE_MS = 40; GAP_STEP_MS = 20; PEAK = 0.25


def gap_ms(k: int) -> int:
    return GAP_BASE_MS + GAP_STEP_MS * (k % CYCLE_CLICKS)


def chirp(sample_rate: int) -> list[float]:
    n = max(2, round(sample_rate * CHIRP_MS / 1000))
    dur = n / sample_rate
    rate = (CHIRP_F1 - CHIRP_F0) / dur
    out = []
    for i in range(n):
        t = i / sample_rate
        window = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))
        out.append(window * math.sin(2 * math.pi * (CHIRP_F0 * t + 0.5 * rate * t * t)))
    return out


def click_times(duration_s: float = DURATION_S) -> list[tuple[float, int]]:
    times, t, k = [], LEAD_IN_S, 0
    while t + PERIOD_S / 2 <= duration_s:
        times.append((t, k)); t += PERIOD_S; k = (k + 1) % CYCLE_CLICKS
    return times


def render_pcm(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    total = round(duration_s * sample_rate)
    mono = [0.0] * total
    template = chirp(sample_rate)
    for t, k in click_times(duration_s):
        for start in (round(t * sample_rate), round((t + gap_ms(k) / 1000) * sample_rate)):
            for i, v in enumerate(template):
                if start + i < total:
                    mono[start + i] = v
    frames = bytearray(total * 4)
    for i, v in enumerate(mono):
        s = int(max(-32767, min(32767, round(v * PEAK * 32767))))
        struct.pack_into("<hh", frames, i * 4, s, s)
    return bytes(frames)


def render_wav(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sample_rate)
        w.writeframes(render_pcm(duration_s=duration_s, sample_rate=sample_rate))
    return out.getvalue()
```

- [ ] **Step 4: Run** the test file — Expected: PASS. Then `uv run python -c "import time,sendspin_sync.click_track as c;t=time.time();c.render_wav();print(round(time.time()-t,1))"` — Expected: a number ≤ 15 (180 s × 48 kHz in pure Python; the bridge caches the bytes once per process, Task 2.2).

- [ ] **Step 5: Commit** `git commit -am "feat(sync): deterministic click track with index-coded chirp pairs"` (add the new files first).

---

### Task 1.3: Estimator (recording → Measurement)

**Files:**
- Create: `src/sendspin_sync/estimator.py`, `tests/unit/sendspin_sync/synth.py`, `tests/unit/sendspin_sync/test_estimator.py`

**Interfaces:**
- Consumes: `click_track.chirp`, `gap_ms`, `PERIOD_S`, `CYCLE_S`, `CYCLE_CLICKS`; `models.Recording`, `models.Measurement`.
- Produces:

```python
MIN_CLICKS = 2; MAX_SPREAD_MS = 3.0; MIN_SNR_DB = 10.0; MIN_SAMPLE_RATE = 12000; MAX_SAMPLE_RATE = 48000
MIN_DURATION_S = 2.0; MAX_DURATION_S = 20.0

def estimate(recording: Recording, *, epoch_s: float = 0.0) -> Measurement
# helpers (tested through estimate, exported for the spike):
def pcm_to_floats(pcm: bytes) -> list[float]
def band_envelope(x: list[float], sample_rate: int, frame_ms: float = 1.0) -> list[float]
def matched_peak(x: list[float], template: list[float], centre: int, half_window: int) -> tuple[int, float]
def circular_mean_ms(phases_ms: list[float], cycle_ms: float) -> tuple[float, float]  # (mean, spread)
```

`estimate` never raises on content: bad geometry (rate out of range, odd byte count, duration out of range) returns `Measurement(status="not_heard", reason="invalid_recording")`; the API layer turns that into 400 (Task 2.5).

- [ ] **Step 1: Synthetic recordings helper** `tests/unit/sendspin_sync/synth.py`:

```python
"""Recordings a phone would make of the click track, with controllable impairments."""
from __future__ import annotations
import math, random, struct
from sendspin_sync import click_track as ct
from sendspin_sync.models import Recording


def synth(member: str, *, lag_s: float, start_time_s: float = 20.0, duration_s: float = 6.0, sample_rate: int = 16000,
          noise: float = 0.01, echo: tuple[float, float] | None = None, gain: float = 0.3, drop: set[int] = frozenset(),
          agc: bool = False, seed: int = 1) -> Recording:
    """A recording that starts at capture time ``start_time_s`` of a track whose epoch is 0 and which this
    speaker plays ``lag_s`` late (positive = later). ``echo`` is (delay_s, amplitude)."""
    rnd = random.Random(seed)
    n = round(duration_s * sample_rate)
    x = [rnd.gauss(0.0, noise) for _ in range(n)]
    template = ct.chirp(sample_rate)
    for idx, (t, k) in enumerate(ct.click_times(ct.DURATION_S)):
        if idx in drop:
            continue
        for chirp_t in (t, t + ct.gap_ms(k) / 1000):
            at = chirp_t + lag_s - start_time_s
            for d, a in ((0.0, gain), *( [echo] if echo else [] )):
                start = round((at + d) * sample_rate)
                if -len(template) < start < n:
                    for i, v in enumerate(template):
                        if 0 <= start + i < n:
                            x[start + i] += a * v
    if agc:  # crude: boost quiet parts, squash loud ones
        x = [math.tanh(3 * v) for v in x]
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, v * 32767))) for v in x))
    return Recording(member=member, start_time_s=start_time_s, sample_rate=sample_rate, pcm=pcm)
```

- [ ] **Step 2: Failing tests**

```python
import struct
import pytest
from sendspin_sync import estimator as est
from sendspin_sync.models import Recording
from tests.unit.sendspin_sync.synth import synth


def _phase(m):
    assert m.status == "ok", m
    return m.phase_ms


def test_clean_recording_gives_phase_equal_to_lag():
    m = est.estimate(synth("a", lag_s=0.123))
    assert m.clicks == 3 and m.confidence > 0.5
    assert abs(_phase(m) - 123.0) <= 1.0 and m.spread_ms <= 1.0


def test_phase_is_relative_to_the_cycle_and_independent_of_start_time():
    a = est.estimate(synth("a", lag_s=0.250, start_time_s=20.0))
    b = est.estimate(synth("a", lag_s=0.250, start_time_s=33.7))
    assert abs(_phase(a) - _phase(b)) <= 1.0


def test_lag_near_cycle_edge_is_measured_modulo_ten_seconds():
    m = est.estimate(synth("a", lag_s=-0.2))
    assert abs(_phase(m) - 9800.0) <= 1.0          # −200 ms ≡ 9800 ms in the 10 s cycle


def test_noise_echo_band_limit_and_agc_are_tolerated():
    m = est.estimate(synth("a", lag_s=0.05, noise=0.05, echo=(0.012, 0.15), agc=True, sample_rate=22050))
    assert abs(_phase(m) - 50.0) <= 2.0


def test_dropped_clicks_lower_the_count_and_one_click_is_not_enough():
    m = est.estimate(synth("a", lag_s=0.0, start_time_s=20.0, drop={9, 10}))   # clicks at 21,23,25 s → indexes 10,11,12; two dropped
    assert m.status == "not_heard" and m.clicks == 1 and m.reason == "too_few_clicks"


def test_silence_is_not_heard_and_garbage_is_invalid():
    n = 6 * 16000
    silent = Recording(member="a", start_time_s=0.0, sample_rate=16000, pcm=bytes(n * 2))
    assert est.estimate(silent).status == "not_heard"
    assert est.estimate(Recording(member="a", start_time_s=0, sample_rate=7000, pcm=bytes(n * 2))).reason == "invalid_recording"
    assert est.estimate(Recording(member="a", start_time_s=0, sample_rate=16000, pcm=bytes(n * 2 + 1))).reason == "invalid_recording"
    assert est.estimate(Recording(member="a", start_time_s=0, sample_rate=16000, pcm=bytes(2 * 16000 // 4))).reason == "invalid_recording"


def test_unstable_when_clicks_disagree():
    # two clicks 4 ms apart in phase: build by concatenating halves with different lags
    a = synth("a", lag_s=0.100, duration_s=3.0)
    b = synth("a", lag_s=0.106, duration_s=3.0, start_time_s=23.0)
    rec = Recording(member="a", start_time_s=20.0, sample_rate=16000, pcm=a.pcm + b.pcm)
    m = est.estimate(rec)
    assert m.status == "unstable" and m.spread_ms is not None and m.spread_ms > 3.0


def test_pure_python_cost_is_bounded():
    import time
    rec = synth("a", lag_s=0.3, duration_s=12.0, noise=0.03)
    t = time.perf_counter()
    est.estimate(rec)
    assert time.perf_counter() - t < 2.0
```

- [ ] **Step 3: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sendspin_sync/test_estimator.py` — Expected: FAIL (`ImportError`).

- [ ] **Step 4: Implement** `src/sendspin_sync/estimator.py`:

```python
"""Find the click track in a phone recording and return its phase in the 10 s cycle."""
from __future__ import annotations
import math, struct
from sendspin_sync import click_track as ct
from sendspin_sync.models import Measurement, Recording

MIN_CLICKS = 2; MAX_SPREAD_MS = 3.0; MIN_SNR_DB = 10.0
MIN_SAMPLE_RATE = 12000; MAX_SAMPLE_RATE = 48000; MIN_DURATION_S = 2.0; MAX_DURATION_S = 20.0
_CANDIDATE_RATIO = 6.0; _FINE_HALF_MS = 15.0; _GAP_TOL_MS = 5.0


def pcm_to_floats(pcm: bytes) -> list[float]:
    return [v / 32768.0 for v in struct.unpack(f"<{len(pcm) // 2}h", pcm)]


def band_envelope(x: list[float], sample_rate: int, frame_ms: float = 1.0) -> list[float]:
    """Energy of the 2–6 kHz band per frame: first difference removes bass, frames sum squares."""
    frame = max(1, round(sample_rate * frame_ms / 1000))
    hp = [x[i] - x[i - 1] for i in range(1, len(x))]
    return [sum(v * v for v in hp[i:i + frame]) for i in range(0, len(hp), frame)]


def matched_peak(x: list[float], template: list[float], centre: int, half_window: int) -> tuple[int, float]:
    lo, hi = max(0, centre - half_window), min(len(x) - len(template), centre + half_window)
    best_i, best = lo, -1.0
    for i in range(lo, hi + 1):
        acc = 0.0
        seg = x[i:i + len(template)]
        for a, b in zip(seg, template, strict=False):
            acc += a * b
        if abs(acc) > best:
            best, best_i = abs(acc), i
    return best_i, best


def circular_mean_ms(phases_ms: list[float], cycle_ms: float) -> tuple[float, float]:
    s = sum(math.sin(2 * math.pi * p / cycle_ms) for p in phases_ms)
    c = sum(math.cos(2 * math.pi * p / cycle_ms) for p in phases_ms)
    mean = (math.atan2(s, c) / (2 * math.pi) * cycle_ms) % cycle_ms
    devs = [((p - mean + cycle_ms / 2) % cycle_ms) - cycle_ms / 2 for p in phases_ms]
    return mean, math.sqrt(sum(d * d for d in devs) / len(devs))


def _invalid(member: str) -> Measurement:
    return Measurement(member=member, status="not_heard", reason="invalid_recording")


def estimate(recording: Recording, *, epoch_s: float = 0.0) -> Measurement:
    sr, member = recording.sample_rate, recording.member
    if not MIN_SAMPLE_RATE <= sr <= MAX_SAMPLE_RATE or len(recording.pcm) % 2:
        return _invalid(member)
    n = len(recording.pcm) // 2
    if not MIN_DURATION_S * sr <= n <= MAX_DURATION_S * sr:
        return _invalid(member)
    x = pcm_to_floats(recording.pcm)
    env = band_envelope(x, sr)
    floor = sorted(env)[len(env) // 2] or 1e-12
    frame = max(1, round(sr / 1000))
    candidates = [i * frame for i, e in enumerate(env) if e > _CANDIDATE_RATIO * floor]
    if not candidates:
        return Measurement(member=member, status="not_heard", reason="no_clicks")
    template = ct.chirp(sr)
    half, min_sep = round(sr * _FINE_HALF_MS / 1000), round(sr * 0.5)
    peaks: list[tuple[int, float]] = []
    for c in candidates:
        if peaks and c - peaks[-1][0] < min_sep // 10:
            continue
        i, p = matched_peak(x, template, c, half)
        if not peaks or i - peaks[-1][0] > len(template):
            peaks.append((i, p))
    noise = math.sqrt(floor / frame) * math.sqrt(sum(v * v for v in template)) or 1e-9
    snr_db = 20 * math.log10(max(p for _, p in peaks) / noise) if peaks else 0.0
    phases: list[float] = []
    used = set()
    for idx, (i, p) in enumerate(peaks):
        if idx in used:
            continue
        best_k, best_p, best_j = None, 0.0, None
        for k in range(ct.CYCLE_CLICKS):
            target = i + round(sr * ct.gap_ms(k) / 1000)
            tol = round(sr * _GAP_TOL_MS / 1000)
            for j, (i2, p2) in enumerate(peaks):
                if j != idx and j not in used and abs(i2 - target) <= tol and p2 > best_p:
                    best_k, best_p, best_j = k, p2, j
        if best_k is None:
            continue
        used.update({idx, best_j})
        t = recording.start_time_s + i / sr
        phases.append(((t - best_k * ct.PERIOD_S - epoch_s) * 1000.0) % (ct.CYCLE_S * 1000.0))
    if len(phases) < MIN_CLICKS:
        return Measurement(member=member, status="not_heard", reason="too_few_clicks", clicks=len(phases))
    mean, spread = circular_mean_ms(phases, ct.CYCLE_S * 1000.0)
    confidence = max(0.0, min(1.0, (snr_db - MIN_SNR_DB) / 30.0)) * (1.0 if spread <= 1.0 else 0.7)
    if snr_db < MIN_SNR_DB:
        return Measurement(member=member, status="not_heard", reason="low_snr", clicks=len(phases), confidence=0.0)
    if spread > MAX_SPREAD_MS:
        return Measurement(member=member, status="unstable", reason="spread", phase_ms=round(mean, 2),
                           clicks=len(phases), spread_ms=round(spread, 2), confidence=confidence)
    return Measurement(member=member, status="ok", phase_ms=round(mean, 2), clicks=len(phases),
                       spread_ms=round(spread, 2), confidence=round(confidence, 3))
```

Tune `_CANDIDATE_RATIO`, the peak de-duplication and the SNR estimate until every test passes; the thresholds in Global Constraints (2 clicks, 3 ms, 10 dB) are fixed.

- [ ] **Step 5: Run** the test file — Expected: PASS (including the 2 s budget; if it fails on a slow CI runner, lower the matched-filter window, not the budget).

- [ ] **Step 6: Commit** `git add src/sendspin_sync/estimator.py tests/unit/sendspin_sync && git commit -m "feat(sync): click-track estimator with index-coded pairs and circular phase"`.

---

### Task 1.4: Planner

**Files:**
- Create: `src/sendspin_sync/planner.py`, `tests/unit/sendspin_sync/test_planner.py`

**Interfaces:**
- Consumes: `Measurement`, `Member`, `Proposal`, `ProposalRow`; `click_track.CYCLE_S`.
- Produces:

```python
MAX_DELAY_MS = 5000; WARN_ABOVE_MS = 2000; VERIFY_SPREAD_MS = 5.0
class PlanError(ValueError): ...   # no usable measurement / unknown reference
def unwrap_lag_ms(phase_ms: float, ref_phase_ms: float, cycle_ms: float = 10_000.0) -> float   # (−cycle/2, cycle/2]
def plan(measurements: Iterable[Measurement], members: Iterable[Member], *, reference: str | None = None) -> Proposal
def spread_ms(measurements: Iterable[Measurement]) -> float    # max lag − min lag among ok rows, unwrapped against the first
```

- [ ] **Step 1: Failing tests**

```python
import random
import pytest
from sendspin_sync.models import Measurement, Member
from sendspin_sync import planner


def mem(i, delay=0, adjustable=True, origin="bridge"):
    return Member(ma_player_id=i, name=i, adjustable=adjustable, origin=origin, delay_ms=delay)


def ok(i, phase):
    return Measurement(member=i, status="ok", phase_ms=phase, confidence=0.9, clicks=3, spread_ms=0.5)


def test_later_player_gets_more_delay_and_minimum_is_normalised_to_zero():
    p = planner.plan([ok("a", 100.0), ok("b", 130.0)], [mem("a", 50), mem("b", 50)])
    rows = {r.member: r for r in p.rows}
    assert p.reference == "a"
    assert (rows["a"].after_ms, rows["b"].after_ms) == (0, 30)        # 50→0 and 80→30 after normalising
    assert rows["b"].delta_ms == -20 and not rows["b"].clamped and p.unverified == ()


def test_lag_unwraps_across_the_cycle_edge():
    assert planner.unwrap_lag_ms(9800.0, 100.0) == pytest.approx(-300.0)
    assert planner.unwrap_lag_ms(100.0, 9800.0) == pytest.approx(300.0)
    p = planner.plan([ok("a", 100.0), ok("b", 9800.0)], [mem("a"), mem("b")])   # b is 300 ms ahead
    rows = {r.member: r for r in p.rows}
    assert (rows["a"].after_ms, rows["b"].after_ms) == (300, 0)


def test_clamping_is_marked_and_residual_predicted_with_warning():
    p = planner.plan([ok("a", 4900.0), ok("b", 0.0)], [mem("a", 300), mem("b", 0)])   # a is 4.9 s late
    rows = {r.member: r for r in p.rows}
    assert rows["a"].after_ms == 5000 and rows["a"].clamped
    assert p.predicted_spread_ms == pytest.approx(200.0)        # wanted 5200, got 5000
    assert any("2000" in w for w in p.warnings)


def test_not_adjustable_and_failed_members_are_left_out_and_listed():
    ms = [ok("a", 0.0), Measurement(member="b", status="not_heard"), ok("c", 20.0)]
    p = planner.plan(ms, [mem("a"), mem("b"), mem("c"), mem("cast", adjustable=False, origin="other")])
    assert {r.member for r in p.rows} == {"a", "c"}
    assert set(p.unverified) == {"b", "cast"}
    rows = {r.member: r for r in p.rows}
    assert (rows["a"].after_ms, rows["c"].after_ms) == (0, 20)      # c is 20 ms late; b unmeasured → no normalisation


def test_no_normalisation_when_a_member_was_skipped():
    ms = [ok("a", 0.0), Measurement(member="b", status="skipped"), ok("c", 20.0)]
    p = planner.plan(ms, [mem("a", 100), mem("b", 100), mem("c", 100)])
    rows = {r.member: r for r in p.rows}
    assert (rows["a"].after_ms, rows["c"].after_ms) == (100, 120)   # c rises, a keeps 100; nothing shifted down


def test_reference_can_be_chosen_and_must_be_measured():
    p = planner.plan([ok("a", 0.0), ok("b", 10.0)], [mem("a"), mem("b")], reference="b")
    assert p.reference == "b"
    with pytest.raises(planner.PlanError):
        planner.plan([Measurement(member="a", status="not_heard")], [mem("a")])


def test_property_alignment_preserved_and_values_in_range():
    rnd = random.Random(7)
    for _ in range(200):
        n = rnd.randint(2, 6)
        members = [mem(f"m{i}", rnd.randint(0, 4000)) for i in range(n)]
        lags = [rnd.uniform(-2000, 2000) for _ in range(n)]
        ms = [ok(m.ma_player_id, (lag + 5000.0) % 10000.0) for m, lag in zip(members, lags, strict=True)]
        p = planner.plan(ms, members)
        rows = {r.member: r for r in p.rows}
        assert all(0 <= r.after_ms <= 5000 for r in p.rows)
        assert min(r.after_ms for r in p.rows) == 0 or any(r.clamped for r in p.rows)
        # alignment: lag_i − lag_j == (after_i − before_i) − (after_j − before_j) wherever nothing clamped
        if not any(r.clamped for r in p.rows):
            for i in range(n):
                for j in range(n):
                    di = rows[f"m{i}"].after_ms - members[i].delay_ms
                    dj = rows[f"m{j}"].after_ms - members[j].delay_ms
                    assert abs((lags[i] - lags[j]) - (di - dj)) <= 1.0
```

(In `test_not_adjustable…` the last assertion documents the rule: `b` was *not heard*, which counts as not measured, so there is no normalisation; `c` is heard 20 ms late and gets 20 ms, `a` keeps 0.)

- [ ] **Step 2: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sendspin_sync/test_planner.py` — Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

```python
"""Turn measured phases into new delays: the later a player sounds, the more delay it gets."""
from __future__ import annotations
from collections.abc import Iterable
from sendspin_sync import click_track as ct
from sendspin_sync.models import Measurement, Member, Proposal, ProposalRow

MAX_DELAY_MS = 5000; WARN_ABOVE_MS = 2000; VERIFY_SPREAD_MS = 5.0


class PlanError(ValueError):
    pass


def unwrap_lag_ms(phase_ms: float, ref_phase_ms: float, cycle_ms: float = ct.CYCLE_S * 1000.0) -> float:
    lag = (phase_ms - ref_phase_ms) % cycle_ms
    return lag - cycle_ms if lag > cycle_ms / 2 else lag


def spread_ms(measurements: Iterable[Measurement]) -> float:
    oks = [m for m in measurements if m.status == "ok" and m.phase_ms is not None]
    if len(oks) < 2:
        return 0.0
    lags = [unwrap_lag_ms(m.phase_ms, oks[0].phase_ms) for m in oks]  # type: ignore[arg-type]
    return max(lags) - min(lags)


def plan(measurements: Iterable[Measurement], members: Iterable[Member], *, reference: str | None = None) -> Proposal:
    members = list(members)
    by_id = {m.ma_player_id: m for m in members}
    oks = {m.member: m for m in measurements if m.status == "ok" and m.phase_ms is not None and by_id.get(m.member) and by_id[m.member].adjustable}
    if not oks:
        raise PlanError("no usable measurement")
    ref = reference or next(iter(oks))
    if ref not in oks:
        raise PlanError(f"reference {ref} was not measured")
    ref_phase = oks[ref].phase_ms or 0.0
    lags = {mid: unwrap_lag_ms(m.phase_ms or 0.0, ref_phase) for mid, m in oks.items()}
    earliest = min(lags.values())
    wanted = {mid: (by_id[mid].delay_ms or 0) + (lags[mid] - earliest) for mid in oks}
    unverified = tuple(m.ma_player_id for m in members if m.ma_player_id not in oks)
    every_adjustable_measured = all(m.ma_player_id in oks for m in members if m.adjustable)
    if every_adjustable_measured:
        floor = min(wanted.values())
        wanted = {mid: v - floor for mid, v in wanted.items()}
    rows, warnings, residuals = [], [], []
    for mid, v in wanted.items():
        before = by_id[mid].delay_ms or 0
        after = max(0, min(MAX_DELAY_MS, round(v)))
        clamped = after != round(v)
        residuals.append(round(v) - after)
        rows.append(ProposalRow(member=mid, before_ms=before, after_ms=after, delta_ms=after - before, clamped=clamped))
    predicted = float(max(residuals) - min(residuals)) if residuals else 0.0
    if any(r.after_ms > WARN_ABOVE_MS for r in rows):
        warnings.append(f"A delay above {WARN_ABOVE_MS} ms needs the server to send audio far ahead; small buffers may underrun.")
    if any(r.clamped for r in rows):
        warnings.append(f"Some delays were clamped to {MAX_DELAY_MS} ms; predicted residual {predicted:.0f} ms.")
    return Proposal(reference=ref, rows=tuple(rows), predicted_spread_ms=round(predicted, 1), unverified=unverified, warnings=tuple(warnings))
```

- [ ] **Step 4: Run** — Expected: PASS. Gates: ruff, mypy.

- [ ] **Step 5: Commit** `git add src/sendspin_sync/planner.py tests/unit/sendspin_sync/test_planner.py && git commit -m "feat(sync): planner with unwrapping, normalisation and clamp reporting"`.

---

### Task 1.5: Ports and the session state machine

**Files:**
- Create: `src/sendspin_sync/ports.py`, `src/sendspin_sync/session.py`, `tests/unit/sendspin_sync/test_session.py`

**Interfaces:**
- Consumes: `models.*`, `planner.plan/spread_ms/VERIFY_SPREAD_MS`, `estimator.estimate`.
- Produces (the bridge adapters implement the ports; the use cases drive `SyncSession`):

```python
# ports.py
class GroupControl(Protocol):
    def members(self, group_id: str) -> list[Member]: ...
    def delays(self, members: Sequence[Member]) -> dict[str, int | None]: ...
    def snapshot(self, group_id: str) -> Snapshot: ...
    def start_signal(self, group_id: str, url: str) -> None: ...
    def stop_signal(self, group_id: str) -> None: ...
    def solo(self, group_id: str, member: str | None) -> None: ...
    def set_delay(self, member: Member, ms: int) -> None: ...
    def restore(self, snapshot: Snapshot) -> None: ...

class ProfileRepository(Protocol):
    def list(self, group_id: str) -> list[Profile]: ...
    def get(self, profile_id: str) -> Profile | None: ...
    def save(self, profile: Profile) -> None: ...
    def delete(self, profile_id: str) -> None: ...

class ClickTrackHost(Protocol):
    def url_for(self, session_id: str) -> str: ...
    def release(self, session_id: str) -> None: ...

class ControlError(Exception):              # adapters raise; carries a stable code
    def __init__(self, code: str, detail: str = ""): ...

# session.py
Mode = Literal["manual", "semi", "auto"]; Point = Literal["device", "position"]
State = Literal["created", "signal_starting", "measuring", "proposed", "applying", "verifying", "restoring", "done", "cancelled", "failed"]
UNDO_WINDOW_S = 30.0; INACTIVITY_S = 900.0; MAX_ROUNDS = 2

class SyncSession:
    def __init__(self, *, session_id: str, group_id: str, mode: Mode, point: Point, control: GroupControl,
                 host: ClickTrackHost, clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[SyncSession], None] | None = None) -> None
    state: State; members: list[Member]; snapshot: Snapshot | None; measurements: dict[str, Measurement]
    proposal: Proposal | None; history: list[dict]; error: str | None; signal_running: bool; soloed: str | None
    undo_until: float | None; round: int; reference: str | None
    def start(self) -> None                      # created: members + snapshot; auto/semi → signal_starting → measuring
    def start_signal(self) -> None; def stop_signal(self) -> None
    def solo(self, member: str | None) -> None; def ab(self, a: str, b: str) -> None   # ab = solo(a); the UI alternates
    def set_reference(self, member: str) -> None
    def record(self, recording: Recording) -> Measurement   # measuring/verifying
    def skip(self, member: str) -> None
    def propose(self) -> Proposal                 # measuring → proposed
    def apply(self, rows: Mapping[str, int] | None = None) -> None   # proposed → applying → (auto: verifying) | (semi: restoring)
    def undo(self) -> None                       # within UNDO_WINDOW_S after an auto apply
    def verify(self) -> None                     # proposed/applying → verifying (new measurement round)
    def write_delay(self, member: str, ms: int) -> None   # manual mode, live
    def reset(self) -> None                      # manual: back to snapshot delays
    def cancel(self) -> None; def fail(self, detail: str) -> None
    def touch(self) -> None; def expired(self) -> bool
    def restore_tick(self) -> bool               # one restore attempt; True when finished (→ done/cancelled/failed)
    def to_dict(self) -> dict                    # the API/event payload
```

`restore_tick` is what the store calls with backoff; `SyncSession` never sleeps. Every transition calls `on_change(self)`.

- [ ] **Step 1: Failing tests** — a fake control that records calls:

```python
import pytest
from sendspin_sync.models import Measurement, Member, Snapshot
from sendspin_sync.ports import ControlError
from sendspin_sync.session import SyncSession
from tests.unit.sendspin_sync.synth import synth


class FakeControl:
    def __init__(self):
        self.calls = []; self.delays_now = {"a": 0, "b": 0}; self.fail_restore = 0
        self.member_list = [Member("a", "A", True, "bridge", delay_ms=0), Member("b", "B", True, "sendspin", delay_ms=0),
                            Member("cast", "Cast", False, "other")]
    def members(self, group_id): return list(self.member_list)
    def delays(self, members): return {m.ma_player_id: self.delays_now.get(m.ma_player_id) for m in members}
    def snapshot(self, group_id):
        return Snapshot(group_id=group_id, delays=dict(self.delays_now), volumes={"a": 40, "b": 40}, muted={"a": False, "b": False}, playing=True)
    def start_signal(self, group_id, url): self.calls.append(("start", url))
    def stop_signal(self, group_id): self.calls.append(("stop",))
    def solo(self, group_id, member): self.calls.append(("solo", member))
    def set_delay(self, member, ms): self.delays_now[member.ma_player_id] = ms; self.calls.append(("set", member.ma_player_id, ms))
    def restore(self, snapshot):
        if self.fail_restore:
            self.fail_restore -= 1; raise ControlError("ma_unavailable")
        self.calls.append(("restore",))


class FakeHost:
    def url_for(self, sid): return f"http://bridge/api/v1/sync/signal/{sid}.wav"
    def release(self, sid): pass


def make(mode="semi", **kw):
    ctl = FakeControl(); events = []
    s = SyncSession(session_id="s1", group_id="g", mode=mode, point="device", control=ctl, host=FakeHost(),
                    clock=kw.pop("clock", None) or (lambda: 0.0), on_change=lambda sess: events.append(sess.state))
    return s, ctl, events


def test_semi_session_runs_measure_propose_apply_restore():
    s, ctl, events = make()
    s.start()
    assert s.state == "measuring" and ("start", "http://bridge/api/v1/sync/signal/s1.wav") in ctl.calls
    assert [m.ma_player_id for m in s.members] == ["a", "b", "cast"]
    s.solo("a"); m = s.record(synth("a", lag_s=0.0)); assert m.status == "ok"
    s.solo("b"); s.record(synth("b", lag_s=0.030))
    p = s.propose()
    assert s.state == "proposed" and {r.member: r.after_ms for r in p.rows} == {"a": 0, "b": 30} and p.unverified == ("cast",)
    s.apply()
    assert ("set", "b", 30) in ctl.calls and ("set", "a", 0) not in ctl.calls      # unchanged rows are not written
    assert s.state == "restoring"
    assert s.restore_tick() is True and s.state == "done"
    assert ctl.calls[-3:] == [("solo", None), ("stop",), ("restore",)]


def test_auto_session_verifies_and_keeps_undo():
    clock = {"t": 100.0}
    s, ctl, _ = make(mode="auto", clock=lambda: clock["t"])
    s.start(); s.record(synth("a", lag_s=0.0)); s.record(synth("b", lag_s=0.030))
    s.propose(); s.apply()
    assert s.state == "verifying" and s.round == 2 and s.undo_until == 130.0
    s.record(synth("a", lag_s=0.0)); s.record(synth("b", lag_s=0.0))     # now aligned
    s.propose()
    assert s.state == "restoring" and s.history[-1]["spread_after_ms"] == pytest.approx(0.0, abs=1.0)
    clock["t"] = 125.0; s.undo()
    assert ctl.delays_now == {"a": 0, "b": 0}
    clock["t"] = 131.0
    with pytest.raises(ControlError): s.undo()


def test_restore_retries_until_it_succeeds_and_cancel_goes_through_restoring():
    s, ctl, _ = make(); s.start(); ctl.fail_restore = 2
    s.cancel()
    assert s.state == "restoring"
    assert s.restore_tick() is False and s.restore_tick() is False and s.restore_tick() is True
    assert s.state == "cancelled" and ("restore",) in ctl.calls


def test_manual_mode_writes_live_and_resets():
    s, ctl, _ = make(mode="manual"); s.start()
    assert s.state == "measuring" and not s.signal_running
    s.write_delay("b", 250); assert ctl.delays_now["b"] == 250
    with pytest.raises(ControlError) as exc: s.write_delay("cast", 10)
    assert exc.value.code == "not_adjustable"
    s.start_signal(); s.ab("a", "b"); assert ("solo", "a") in ctl.calls
    s.reset(); assert ctl.delays_now["b"] == 0


def test_apply_failure_keeps_the_proposal_and_moves_nothing_else():
    s, ctl, _ = make(); s.start(); s.record(synth("a", lag_s=0.0)); s.record(synth("b", lag_s=0.030)); s.propose()
    def boom(member, ms): raise ControlError("ma_forbidden")
    ctl.set_delay = boom
    with pytest.raises(ControlError): s.apply()
    assert s.state == "proposed" and s.proposal is not None


def test_inactivity_expiry_and_member_gone():
    clock = {"t": 0.0}; s, ctl, _ = make(clock=lambda: clock["t"]); s.start()
    clock["t"] = 901.0; assert s.expired()
    with pytest.raises(ControlError) as exc: s.record(synth("zz", lag_s=0.0))
    assert exc.value.code == "member_gone"


def test_to_dict_is_json_shaped():
    import json
    s, _, _ = make(); s.start(); s.record(synth("a", lag_s=0.0))
    d = json.loads(json.dumps(s.to_dict()))
    assert d["id"] == "s1" and d["state"] == "measuring" and d["measurements"]["a"]["status"] == "ok" and "pcm" not in json.dumps(d)
```

- [ ] **Step 2: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sendspin_sync/test_session.py` — Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement** `ports.py` (the Protocols above and `ControlError(code, detail)` with `.code`/`.detail`) and `session.py`:

```python
"""One tuning session: the state machine the spec's "Session" section describes."""
from __future__ import annotations
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal
from sendspin_sync import estimator, planner
from sendspin_sync.models import Measurement, Member, Proposal, Recording, Snapshot, to_dict
from sendspin_sync.ports import ClickTrackHost, ControlError, GroupControl

Mode = Literal["manual", "semi", "auto"]; Point = Literal["device", "position"]
State = Literal["created", "signal_starting", "measuring", "proposed", "applying", "verifying", "restoring", "done", "cancelled", "failed"]
UNDO_WINDOW_S = 30.0; INACTIVITY_S = 900.0; MAX_ROUNDS = 2


class SyncSession:
    def __init__(self, *, session_id: str, group_id: str, mode: Mode, point: Point, control: GroupControl,
                 host: ClickTrackHost, clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[SyncSession], None] | None = None) -> None:
        self.id, self.group_id, self.mode, self.point = session_id, group_id, mode, point
        self._control, self._host, self._clock, self._on_change = control, host, clock, on_change
        self.state: State = "created"; self.members: list[Member] = []; self.snapshot: Snapshot | None = None
        self.measurements: dict[str, Measurement] = {}; self.proposal: Proposal | None = None
        self.history: list[dict[str, Any]] = []; self.error: str | None = None
        self.signal_running = False; self.soloed: str | None = None; self.undo_until: float | None = None
        self.round = 1; self.reference: str | None = None; self._final: State = "done"
        self._undo_delays: dict[str, int] = {}; self._written: dict[str, int] = {}
        self.created_at = clock(); self.last_activity = clock()

    # -- helpers -------------------------------------------------------------------
    def _set(self, state: State) -> None:
        self.state = state
        if self._on_change: self._on_change(self)
    def touch(self) -> None: self.last_activity = self._clock()
    def expired(self) -> bool: return self._clock() - self.last_activity > INACTIVITY_S
    def _member(self, mid: str) -> Member:
        for m in self.members:
            if m.ma_player_id == mid: return m
        raise ControlError("member_gone", f"{mid} is not in the group")
    def _require(self, *states: State) -> None:
        if self.state not in states: raise ControlError("session_state", f"not allowed in {self.state}")

    # -- lifecycle -----------------------------------------------------------------
    def start(self) -> None:
        self._require("created")
        self.members = self._control.members(self.group_id)
        self.snapshot = self._control.snapshot(self.group_id)
        self._set("measuring") if self.mode == "manual" else self._begin_signal()

    def _begin_signal(self) -> None:
        self._set("signal_starting"); self.start_signal(); self._set("measuring")

    def start_signal(self) -> None:
        self._control.start_signal(self.group_id, self._host.url_for(self.id)); self.signal_running = True; self.touch()
    def stop_signal(self) -> None:
        self._control.stop_signal(self.group_id); self.signal_running = False; self.touch()
    def solo(self, member: str | None) -> None:
        if member is not None: self._member(member)
        self._control.solo(self.group_id, member); self.soloed = member; self.touch()
    def ab(self, a: str, b: str) -> None:
        self._member(a); self._member(b); self.solo(a)
    def set_reference(self, member: str) -> None:
        self._member(member); self.reference = member; self.touch()

    # -- measuring -----------------------------------------------------------------
    def record(self, recording: Recording) -> Measurement:
        self._require("measuring", "verifying"); self._member(recording.member)
        m = estimator.estimate(recording); self.measurements[recording.member] = m; self.touch()
        if self._on_change: self._on_change(self)
        return m
    def skip(self, member: str) -> None:
        self._require("measuring", "verifying"); self._member(member)
        self.measurements[member] = Measurement(member=member, status="skipped", reason="skipped"); self.touch()
        if self._on_change: self._on_change(self)

    def propose(self) -> Proposal:
        self._require("measuring", "verifying")
        spread = planner.spread_ms(self.measurements.values())
        if self.state == "verifying":
            self.history[-1]["spread_after_ms"] = round(spread, 1)
            assert self.proposal is not None
            if self.mode == "auto" and spread > planner.VERIFY_SPREAD_MS and self.round <= MAX_ROUNDS:
                self.proposal = planner.plan(self.measurements.values(), self.members, reference=self.reference)
                self.history.append({"round": self.round, "spread_before_ms": round(spread, 1)})
                self._set("proposed"); self.apply(); return self.proposal
            self._begin_restore("done"); return self.proposal
        try:
            self.proposal = planner.plan(self.measurements.values(), self.members, reference=self.reference)
        except planner.PlanError as exc:
            raise ControlError("no_measurement", str(exc)) from exc
        self.history.append({"round": self.round, "spread_before_ms": round(spread, 1)})
        self._set("proposed"); return self.proposal

    # -- applying ------------------------------------------------------------------
    def apply(self, rows: Mapping[str, int] | None = None) -> None:
        self._require("proposed"); assert self.proposal is not None
        wanted = dict(rows) if rows is not None else {r.member: r.after_ms for r in self.proposal.rows}
        self._set("applying")
        try:
            for mid, ms in wanted.items():
                member = self._member(mid)
                if not member.adjustable: raise ControlError("not_adjustable", member.name)
                before = member.delay_ms or 0
                if ms != before:
                    self._control.set_delay(member, ms); self._undo_delays.setdefault(mid, before); self._written[mid] = ms
        except ControlError:
            self._set("proposed"); raise
        self.members = [Member(**{**m.__dict__, "delay_ms": wanted.get(m.ma_player_id, m.delay_ms)}) for m in self.members]
        self.touch()
        if self.mode == "auto" and self.round <= MAX_ROUNDS:
            self.undo_until = self._clock() + UNDO_WINDOW_S; self.verify()
        else:
            self._begin_restore("done")

    def verify(self) -> None:
        self._require("applying", "proposed"); self.round += 1; self.measurements = {}
        if not self.signal_running: self.start_signal()
        self._set("verifying")

    def undo(self) -> None:
        if self.undo_until is None or self._clock() > self.undo_until: raise ControlError("undo_expired")
        for mid, ms in self._undo_delays.items(): self._control.set_delay(self._member(mid), ms)
        self._undo_delays.clear(); self.undo_until = None; self.touch()

    # -- manual --------------------------------------------------------------------
    def write_delay(self, member: str, ms: int) -> None:
        m = self._member(member)
        if not m.adjustable: raise ControlError("not_adjustable", m.name)
        self._control.set_delay(m, max(0, min(5000, int(ms)))); self._written[member] = ms; self.touch()
    def reset(self) -> None:
        assert self.snapshot is not None
        for mid in list(self._written): self._control.set_delay(self._member(mid), self.snapshot.delays.get(mid, 0))
        self._written.clear(); self.touch()

    # -- ending --------------------------------------------------------------------
    def _begin_restore(self, final: State) -> None:
        self._final = final; self._set("restoring")
    def cancel(self) -> None:
        if self.state in ("done", "cancelled", "failed"): return
        self._begin_restore("cancelled")
    def fail(self, detail: str) -> None:
        self.error = detail; self._begin_restore("failed")

    def restore_tick(self) -> bool:
        self._require("restoring")
        try:
            if self.soloed is not None: self._control.solo(self.group_id, None); self.soloed = None
            if self.signal_running: self._control.stop_signal(self.group_id); self.signal_running = False
            if self.snapshot is not None: self._control.restore(self.snapshot)
        except ControlError as exc:
            self.error = exc.code; return False
        self._host.release(self.id); self._set(self._final); return True

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "group_id": self.group_id, "mode": self.mode, "point": self.point, "state": self.state,
                "members": [to_dict(m) for m in self.members], "measurements": {k: to_dict(v) for k, v in self.measurements.items()},
                "proposal": to_dict(self.proposal) if self.proposal else None, "history": list(self.history),
                "error": self.error, "signal_running": self.signal_running, "soloed": self.soloed, "reference": self.reference,
                "round": self.round, "undo_until_s": (None if self.undo_until is None else max(0.0, self.undo_until - self._clock()))}
```

Round accounting: round 1 measures and applies; `verify()` bumps to round 2 (the verification pass); a refinement applies in round 2 and, because `apply` only verifies while `round <= MAX_ROUNDS` before the bump, ends in `restoring` after its own verification — at most two applies, as the spec requires.

- [ ] **Step 4: Run** — Expected: PASS. Gates: ruff, mypy (`src/sendspin_sync/`).

- [ ] **Step 5: Commit** `git add src/sendspin_sync/ports.py src/sendspin_sync/session.py tests/unit/sendspin_sync/test_session.py && git commit -m "feat(sync): session state machine and ports"`.

---

## Stage 2 — Bridge adapters and API

### Task 2.1: `SYNC_PROFILES` in config, schema, HA translation; `ConfigProfileRepository`

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`DEFAULT_CONFIG`), `src/sendspin_bridge/config/schema.json`, `scripts/translate_ha_config.py` (`web_ui_only_keys`)
- Create: `src/sendspin_bridge/sync_tuning/__init__.py`, `src/sendspin_bridge/sync_tuning/profiles.py`, `tests/unit/sync_tuning/__init__.py`, `tests/unit/sync_tuning/test_profiles.py`
- Test (modify): `tests/integration/test_translate_ha_config.py`

**Interfaces:**
- Consumes: `update_config(mutator)`, `load_config()`, `config_lock` from `sendspin_bridge.config`; `Profile`, `profile_from_dict`, `to_dict` from `sendspin_sync.models`.
- Produces: `class ConfigProfileRepository` implementing `ProfileRepository` (`list(group_id)`, `get(id)`, `save(profile)`, `delete(id)`), ids `uuid4` strings assigned by the caller.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/sync_tuning/test_profiles.py
import json
from sendspin_sync.models import Profile
from sendspin_bridge.sync_tuning.profiles import ConfigProfileRepository


def _profile(pid="p1", group="g1", name="Evening"):
    return Profile(id=pid, name=name, group_id=group, kind="device", delays={"a": 120, "b": 0}, created_at="2026-10-15T10:00:00Z")


def test_save_list_get_delete_round_trip(tmp_config):
    repo = ConfigProfileRepository()
    repo.save(_profile()); repo.save(_profile("p2", "g2"))
    assert [p.id for p in repo.list("g1")] == ["p1"] and repo.get("p2").group_id == "g2"
    assert json.loads(tmp_config.read_text())["SYNC_PROFILES"][0]["delays"] == {"a": 120, "b": 0}
    repo.save(_profile(name="Evening 2"))                      # same id → replaced
    assert [p.name for p in repo.list("g1")] == ["Evening 2"]
    repo.delete("p1"); assert repo.list("g1") == [] and repo.get("p1") is None


def test_default_config_has_an_empty_profile_list():
    from sendspin_bridge.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["SYNC_PROFILES"] == []
```

Add to `tests/integration/test_translate_ha_config.py`:

```python
def test_sync_profiles_survive_addon_restart(tmp_path):
    profiles = [{"id": "p1", "name": "Evening", "group_id": "g", "kind": "device", "delays": {"a": 120}, "created_at": "2026-10-15T10:00:00Z", "notes": ""}]
    _write_json(tmp_path / "config.json", {"SYNC_PROFILES": profiles})
    _write_json(tmp_path / "options.json", _minimal_options())
    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()
    assert _read_json(tmp_path / "config.json")["SYNC_PROFILES"] == profiles
```

(`tests/unit/config/test_config.py::test_default_config_keys_present_in_json_schema` goes red on its own once the key is in `DEFAULT_CONFIG` and not in the schema.)

- [ ] **Step 2: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sync_tuning tests/unit/config/test_config.py tests/integration/test_translate_ha_config.py` — Expected: FAIL.

- [ ] **Step 3: Implement.** `DEFAULT_CONFIG`: add `"SYNC_PROFILES": [],` next to `"AUTH_TOKENS": []`. `schema.json` property:

```json
"SYNC_PROFILES": {
  "type": "array", "default": [],
  "description": "Named per-group delay profiles for Music Assistant sync groups (managed by the Sync screen, not the settings form).",
  "items": {"type": "object", "required": ["id", "name", "group_id", "delays"], "properties": {
    "id": {"type": "string"}, "name": {"type": "string"}, "group_id": {"type": "string"},
    "kind": {"type": "string", "enum": ["device", "position"]},
    "delays": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0, "maximum": 5000}},
    "created_at": {"type": "string"}, "notes": {"type": "string"}}}
}
```

`translate_ha_config.py`: append `"SYNC_PROFILES",` to `web_ui_only_keys`. `profiles.py`:

```python
"""Profiles live in config.json under SYNC_PROFILES."""
from __future__ import annotations
from sendspin_bridge.config import load_config, update_config
from sendspin_sync.models import Profile, profile_from_dict, to_dict


class ConfigProfileRepository:
    def _all(self) -> list[Profile]:
        return [profile_from_dict(p) for p in load_config().get("SYNC_PROFILES") or [] if isinstance(p, dict) and p.get("id")]
    def list(self, group_id: str) -> list[Profile]:
        return [p for p in self._all() if p.group_id == group_id]
    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self._all() if p.id == profile_id), None)
    def save(self, profile: Profile) -> None:
        def mutate(cfg: dict) -> None:
            rows = [p for p in cfg.get("SYNC_PROFILES") or [] if p.get("id") != profile.id]
            rows.append(to_dict(profile)); cfg["SYNC_PROFILES"] = rows
        update_config(mutate)
    def delete(self, profile_id: str) -> None:
        update_config(lambda cfg: cfg.__setitem__("SYNC_PROFILES", [p for p in cfg.get("SYNC_PROFILES") or [] if p.get("id") != profile_id]))
```

Also exclude the key from the settings form: in `application/config.py` add `- {"SYNC_PROFILES"}` to `_ALLOWED_POST_CONFIG_KEYS` (it stays in export/import because export reads the file).

- [ ] **Step 4: Run** — Expected: PASS (all three files). Gates.

- [ ] **Step 5: Commit** `git add -A src/sendspin_bridge/config src/sendspin_bridge/sync_tuning src/sendspin_bridge/application/config.py scripts/translate_ha_config.py tests && git commit -m "feat(sync): SYNC_PROFILES config key and profile repository"`.

---

### Task 2.2: Click-track host and the public signal route

**Files:**
- Create: `src/sendspin_bridge/sync_tuning/click_track_host.py`, `src/sendspin_bridge/api/routers/sync.py` (public router only, extended in Task 2.5), `tests/unit/sync_tuning/test_click_track_host.py`, `tests/unit/api/test_sync_signal.py`
- Modify: `src/sendspin_bridge/api/app.py` (`v1.include_router(sync.public)` next to `bridge.public`)

**Interfaces:**
- Consumes: `sendspin_sync.click_track.render_wav`; `config.network.get_local_ip`, `resolve_web_port`.
- Produces:

```python
# click_track_host.py
class ClickTrackTokens:                       # process singleton `tokens`
    def issue(self, session_id: str) -> str   # 32 random bytes → 64 hex chars
    def session_for(self, token: str) -> str | None   # constant-time lookup
    def release(self, session_id: str) -> None
def track_wav() -> bytes                       # rendered once per process (lru_cache)
class FastApiClickTrackHost:                   # implements ClickTrackHost
    def __init__(self, tokens: ClickTrackTokens = tokens, base_url: Callable[[], str] = default_base_url)
    def url_for(self, session_id: str) -> str  # f"{base_url()}/api/v1/sync/signal/{token}.wav"
    def release(self, session_id: str) -> None
def default_base_url() -> str                  # f"http://{get_local_ip()}:{resolve_web_port()}"
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/sync_tuning/test_click_track_host.py
from sendspin_bridge.sync_tuning.click_track_host import ClickTrackTokens, FastApiClickTrackHost, track_wav


def test_tokens_are_random_session_scoped_and_released():
    t = ClickTrackTokens()
    a, b = t.issue("s1"), t.issue("s2")
    assert len(a) == 64 and a != b and t.session_for(a) == "s1"
    assert t.issue("s1") != a and t.session_for(a) is None          # re-issue invalidates the old token
    t.release("s2"); assert t.session_for(b) is None
    assert t.session_for("nope") is None


def test_url_uses_the_lan_address_and_web_port():
    t = ClickTrackTokens()
    host = FastApiClickTrackHost(tokens=t, base_url=lambda: "http://192.168.1.2:8080")
    url = host.url_for("s1")
    assert url.startswith("http://192.168.1.2:8080/api/v1/sync/signal/") and url.endswith(".wav") and "tts" not in url
    host.release("s1"); assert t.session_for(url.rsplit("/", 1)[1][:-4]) is None


def test_track_is_rendered_once():
    assert track_wav() is track_wav() and track_wav()[:4] == b"RIFF"
```

```python
# tests/unit/api/test_sync_signal.py
"""The click track is fetched by Music Assistant without credentials; only the token guards it."""
from sendspin_bridge.sync_tuning import click_track_host as host
from tests.support.api_client import make_client


def test_signal_is_public_with_a_live_token_and_404_otherwise(monkeypatch):
    tokens = host.ClickTrackTokens(); monkeypatch.setattr(host, "tokens", tokens)
    token = tokens.issue("s1")
    client = make_client({"AUTH_ENABLED": True})                  # no session, no bearer
    resp = client.get(f"/api/v1/sync/signal/{token}.wav")
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/wav" and resp.content[:4] == b"RIFF"
    assert resp.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/sync/signal/" + "0" * 64 + ".wav").status_code == 404
    tokens.release("s1")
    assert client.get(f"/api/v1/sync/signal/{token}.wav").status_code == 404
```

- [ ] **Step 2: Run** both files — Expected: FAIL (`ImportError`, 401/404).

- [ ] **Step 3: Implement**

```python
# click_track_host.py
from __future__ import annotations
import hmac, secrets, threading
from collections.abc import Callable
from functools import lru_cache
from sendspin_bridge.config.network import get_local_ip, resolve_web_port
from sendspin_sync.click_track import render_wav


@lru_cache(maxsize=1)
def track_wav() -> bytes:
    return render_wav()


class ClickTrackTokens:
    def __init__(self) -> None:
        self._lock = threading.Lock(); self._by_session: dict[str, str] = {}
    def issue(self, session_id: str) -> str:
        token = secrets.token_hex(32)
        with self._lock: self._by_session[session_id] = token
        return token
    def session_for(self, token: str) -> str | None:
        with self._lock: items = list(self._by_session.items())
        for sid, known in items:
            if hmac.compare_digest(known, token): return sid
        return None
    def release(self, session_id: str) -> None:
        with self._lock: self._by_session.pop(session_id, None)


tokens = ClickTrackTokens()


def default_base_url() -> str:
    return f"http://{get_local_ip() or '127.0.0.1'}:{resolve_web_port()}"


class FastApiClickTrackHost:
    def __init__(self, tokens: ClickTrackTokens = tokens, base_url: Callable[[], str] = default_base_url) -> None:
        self._tokens, self._base_url = tokens, base_url
    def url_for(self, session_id: str) -> str:
        return f"{self._base_url().rstrip('/')}/api/v1/sync/signal/{self._tokens.issue(session_id)}.wav"
    def release(self, session_id: str) -> None:
        self._tokens.release(session_id)
```

```python
# api/routers/sync.py (first version)
"""``/sync`` — group sync tuning. ``public`` serves the click track to Music Assistant."""
from __future__ import annotations
from fastapi import APIRouter, Response
from sendspin_bridge.api.errors import ApiError
from sendspin_bridge.sync_tuning import click_track_host as host

public = APIRouter(tags=["sync"])


@public.get("/sync/signal/{token}.wav", response_class=Response, summary="Click track for a running session (public)")
def signal(token: str) -> Response:
    if len(token) != 64 or host.tokens.session_for(token) is None:
        raise ApiError(404, "not_found", "No session owns this token")
    return Response(host.track_wav(), media_type="audio/wav", headers={"Cache-Control": "no-store"})
```

`app.py`: `from sendspin_bridge.api.routers import sync` and `v1.include_router(sync.public)` after `bridge.public`.

- [ ] **Step 4: Run** — Expected: PASS. Gates.

- [ ] **Step 5: Commit** `git add src/sendspin_bridge/sync_tuning/click_track_host.py src/sendspin_bridge/api/routers/sync.py src/sendspin_bridge/api/app.py tests/unit/sync_tuning/test_click_track_host.py tests/unit/api/test_sync_signal.py && git commit -m "feat(sync): public tokenised click-track route"`.

---

### Task 2.3: Music Assistant API wrapper and `MaGroupControl`

Branches on the stage 0 note: **(a)** `AnnouncementSignal` + `MaMuteSolo` (default below); **(c)** add `HybridSolo`; **(b)** `QueueSignal`. Implement only the chosen branch plus the shared parts; delete the others' code from this task when executing.

**Files:**
- Create: `src/sendspin_bridge/sync_tuning/ma_api.py`, `src/sendspin_bridge/sync_tuning/ma_group_control.py`, `tests/unit/sync_tuning/fake_ma.py`, `tests/unit/sync_tuning/test_ma_group_control.py`

**Interfaces:**
- Consumes: `get_ma_api_credentials()`, `get_ma_groups()`, `get_ma_player_ids()` from `services/music_assistant/ma_runtime_state.py`; `run_on_loop`/`submit_on_loop`; `music_assistant_client.MusicAssistantClient`; for (c) `application.playback.set_device_mute(device_id, muted)` and `application.status.find_client`.
- Produces:

```python
# ma_api.py
class MaApiError(Exception): code: str; detail: str          # code: "ma_unavailable" | "ma_forbidden" | "ma_error"
class MaApi(Protocol):
    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any: ...
    def fire(self, command: str, args: dict[str, Any]) -> None: ...
class MaWsApi(MaApi):                                          # one MusicAssistantClient per instance, lazily connected on the bridge loop
    def __init__(self, url: str, token: str) -> None
    def close(self) -> None
def ma_api_from_config() -> MaWsApi                             # raises MaApiError("ma_unavailable") without credentials

# ma_group_control.py
class SignalStrategy(Protocol): start(group_id, url); stop(group_id)
class SoloStrategy(Protocol): solo(group_id, members: list[Member], member: str | None); clear(group_id, members)
class AnnouncementSignal(SignalStrategy); class QueueSignal(SignalStrategy)
class MaMuteSolo(SoloStrategy); class HybridSolo(SoloStrategy)
class MaGroupControl(GroupControl):
    def __init__(self, api: MaApi, *, signal: SignalStrategy, solo: SoloStrategy, groups: Callable[[], list[dict]] = get_ma_groups,
                 bridge_player_ids: Callable[[], set[str]] = _bridge_ids) -> None
def default_group_control() -> MaGroupControl   # wires the stage 0 choice
```

Member resolution rules (from the spec's "Facts"): group = cached sync group `{id, name, members[{id,name}]}`; for each member id, the player state from `players/all {return_protocol_players: true}`: if `provider == "sendspin"` → `client_id = player_id`, `origin = "bridge"` when the id is one of the bridge's own player ids else `"sendspin"`; else if its `output_protocols[]` has an entry with `protocol_domain == "sendspin"` → `client_id = output_protocol_id`, delay key `f"{client_id}||protocol||sendspin_static_delay"`, `origin = "sendspin"`; else `origin = "other"`, `adjustable = False`. `adjustable` is true only when `config/players/get_entries {player_id: client_id}` contains the key `sendspin_static_delay`; `delay_ms` from `config/players/get_value {player_id: client_id, key: "sendspin_static_delay"}` (default 0).

- [ ] **Step 1: Fake Music Assistant** `tests/unit/sync_tuning/fake_ma.py` — an in-memory `MaApi` that records calls and models the facts above:

```python
from __future__ import annotations
from typing import Any
from sendspin_bridge.sync_tuning.ma_api import MaApiError


class FakeMa:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []; self.forbid_write = False
        self.players = {
            "spk-bridge": {"player_id": "spk-bridge", "provider": "sendspin", "type": "player", "name": "Kitchen", "volume_level": 40, "volume_muted": False, "playback_state": "playing", "output_protocols": []},
            "spk-kiosk": {"player_id": "spk-kiosk", "provider": "sendspin", "type": "player", "name": "Kiosk", "volume_level": 30, "volume_muted": True, "playback_state": "playing", "output_protocols": []},
            "uni": {"player_id": "uni", "provider": "universal_player", "type": "player", "name": "Hall", "volume_level": 50, "volume_muted": False, "playback_state": "playing",
                    "output_protocols": [{"output_protocol_id": "uni-sendspin", "protocol_domain": "sendspin", "is_native": False}]},
            "uni-sendspin": {"player_id": "uni-sendspin", "provider": "sendspin", "type": "protocol", "name": "Hall (Sendspin)"},
            "cast": {"player_id": "cast", "provider": "chromecast", "type": "player", "name": "TV", "volume_level": 20, "volume_muted": False, "playback_state": "playing", "output_protocols": []},
            "grp": {"player_id": "grp", "provider": "sync_group", "type": "group", "name": "Downstairs", "playback_state": "playing", "group_members": ["spk-bridge", "spk-kiosk", "uni", "cast"]},
        }
        self.entries = {"spk-bridge": ["sendspin_static_delay"], "spk-kiosk": [], "uni-sendspin": ["sendspin_static_delay"], "cast": []}
        self.values = {("spk-bridge", "sendspin_static_delay"): 120, ("uni-sendspin", "sendspin_static_delay"): 0}

    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any:
        self.calls.append((command, args))
        if command == "players/all": return list(self.players.values())
        if command == "players/get": return self.players.get(args["player_id"])
        if command == "config/players/get_entries": return [{"key": k} for k in self.entries.get(args["player_id"], [])]
        if command == "config/players/get_value": return self.values.get((args["player_id"], args["key"]), args.get("default", 0))
        if command == "config/players/save":
            if self.forbid_write: raise MaApiError("ma_forbidden", "scope config.players.write required")
            for key, value in args["values"].items():
                pid, _, real = key.partition("||protocol||") if "||protocol||" in key else (args["player_id"], "", key)
                self.values[(pid, real)] = int(value)
            return {}
        if command == "players/cmd/volume_mute": self.players[args["player_id"]]["volume_muted"] = args["muted"]; return None
        if command == "players/cmd/volume_set": self.players[args["player_id"]]["volume_level"] = args["volume_level"]; return None
        if command == "players/cmd/stop": self.players[args["player_id"]]["playback_state"] = "idle"; return None
        raise MaApiError("ma_error", f"unknown {command}")

    def fire(self, command: str, args: dict[str, Any]) -> None:
        self.calls.append((command, args))
```

- [ ] **Step 2: Failing tests**

```python
import pytest
from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_bridge.sync_tuning.ma_group_control import AnnouncementSignal, MaGroupControl, MaMuteSolo
from sendspin_sync.ports import ControlError
from tests.unit.sync_tuning.fake_ma import FakeMa

GROUPS = [{"id": "grp", "name": "Downstairs", "members": [{"id": m, "name": m} for m in ("spk-bridge", "spk-kiosk", "uni", "cast")]}]


def make(ma=None):
    ma = ma or FakeMa()
    ctl = MaGroupControl(ma, signal=AnnouncementSignal(ma), solo=MaMuteSolo(ma), groups=lambda: GROUPS, bridge_player_ids=lambda: {"spk-bridge"})
    return ctl, ma


def test_members_resolve_origin_adjustability_and_delay():
    ctl, _ = make()
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    assert (by["spk-bridge"].origin, by["spk-bridge"].adjustable, by["spk-bridge"].delay_ms, by["spk-bridge"].client_id) == ("bridge", True, 120, "spk-bridge")
    assert (by["spk-kiosk"].origin, by["spk-kiosk"].adjustable) == ("sendspin", False)        # no delay entry → cannot be adjusted
    assert (by["uni"].origin, by["uni"].adjustable, by["uni"].client_id, by["uni"].delay_ms) == ("sendspin", True, "uni-sendspin", 0)
    assert (by["cast"].origin, by["cast"].adjustable) == ("other", False)


def test_set_delay_writes_the_protocol_key_form_for_a_universal_player():
    ctl, ma = make()
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    ctl.set_delay(by["uni"], 250); ctl.set_delay(by["spk-bridge"], 90)
    assert ("config/players/save", {"player_id": "uni", "values": {"uni-sendspin||protocol||sendspin_static_delay": 250}}) in ma.calls
    assert ("config/players/save", {"player_id": "spk-bridge", "values": {"sendspin_static_delay": 90}}) in ma.calls
    with pytest.raises(ControlError) as exc: ctl.set_delay(by["cast"], 10)
    assert exc.value.code == "not_adjustable"


def test_forbidden_write_becomes_ma_forbidden():
    ma = FakeMa(); ma.forbid_write = True; ctl, _ = make(ma)
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    with pytest.raises(ControlError) as exc: ctl.set_delay(by["spk-bridge"], 1)
    assert exc.value.code == "ma_forbidden"


def test_snapshot_solo_and_restore_only_touch_what_changed():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    assert snap.muted == {"spk-bridge": False, "spk-kiosk": True, "uni": False, "cast": False} and snap.playing
    members = ctl.members("grp")
    ctl.solo("grp", "spk-bridge")
    assert ma.players["uni"]["volume_muted"] and ma.players["cast"]["volume_muted"] and not ma.players["spk-bridge"]["volume_muted"]
    ctl.solo("grp", None)
    assert not ma.players["uni"]["volume_muted"] and ma.players["spk-kiosk"]["volume_muted"]   # the kiosk was muted before: left muted
    ma.players["uni"]["volume_muted"] = True                                                    # pretend MA's own restore missed it
    ctl.restore(snap)
    assert not ma.players["uni"]["volume_muted"] and ma.players["spk-kiosk"]["volume_muted"]
    assert ("players/cmd/volume_mute", {"player_id": "spk-kiosk", "muted": False}) not in ma.calls


def test_announcement_signal_is_fired_not_awaited_and_stop_stops_the_group():
    ctl, ma = make()
    ctl.start_signal("grp", "http://192.168.1.2:8080/api/v1/sync/signal/abc.wav")
    assert ("players/cmd/play_announcement", {"player_id": "grp", "url": "http://192.168.1.2:8080/api/v1/sync/signal/abc.wav", "pre_announce": False}) == ma.calls[-1]
    ctl.stop_signal("grp")
    assert ma.calls[-1] == ("players/cmd/stop", {"player_id": "grp"}) and ma.players["grp"]["playback_state"] == "idle"
```

- [ ] **Step 3: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sync_tuning/test_ma_group_control.py` — Expected: FAIL (`ImportError`).

- [ ] **Step 4: Implement** `ma_api.py`:

```python
"""A thin synchronous face over Music Assistant's WebSocket API for the sync adapters."""
from __future__ import annotations
import asyncio, logging
from typing import Any, Protocol
from sendspin_bridge.application.runtime import run_on_loop, submit_on_loop
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials

logger = logging.getLogger(__name__)


class MaApiError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code); self.code, self.detail = code, detail


class MaApi(Protocol):
    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any: ...
    def fire(self, command: str, args: dict[str, Any]) -> None: ...


def _classify(exc: Exception) -> MaApiError:
    text = str(exc).lower()
    if any(w in text for w in ("forbidden", "permission", "scope", "unauthorized", "403", "401")):
        return MaApiError("ma_forbidden", str(exc))
    if isinstance(exc, (ConnectionError, OSError, asyncio.TimeoutError)) or "not connected" in text:
        return MaApiError("ma_unavailable", str(exc))
    return MaApiError("ma_error", str(exc))


class MaWsApi:
    def __init__(self, url: str, token: str) -> None:
        self._url, self._token, self._client = url if "://" in url else f"http://{url}", token, None
        self._lock = asyncio.Lock()

    async def _connected(self):
        from music_assistant_client import MusicAssistantClient
        async with self._lock:
            if self._client is None:
                client = MusicAssistantClient(self._url, None, token=self._token)
                await client.__aenter__(); await client.connect()
                self._client = client
            return self._client

    async def _call(self, command: str, args: dict[str, Any]) -> Any:
        client = await self._connected()
        return await client.send_command(command, **args)

    async def _fire(self, command: str, args: dict[str, Any]) -> None:
        client = await self._connected()
        await client.send_command_no_wait(command, **args)

    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any:
        try:
            return run_on_loop(self._call(command, args), timeout=timeout, description=f"MA {command}")
        except MaApiError: raise
        except Exception as exc: raise _classify(exc) from exc

    def fire(self, command: str, args: dict[str, Any]) -> None:
        if not submit_on_loop(self._fire(command, args), description=f"MA {command}"):
            raise MaApiError("ma_unavailable", "bridge loop not running")

    def close(self) -> None:
        async def _close():
            if self._client is not None:
                await self._client.__aexit__(None, None, None); self._client = None
        submit_on_loop(_close(), description="MA close")


def ma_api_from_config() -> MaWsApi:
    url, token = get_ma_api_credentials()
    if not url or not token: raise MaApiError("ma_unavailable", "Music Assistant is not connected")
    return MaWsApi(url, token)
```

`ma_group_control.py`:

```python
"""GroupControl over Music Assistant: members, delays, mute-based solo and the stage 0 signal path."""
from __future__ import annotations
from collections.abc import Callable
from typing import Any, Protocol
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_groups, get_ma_player_ids
from sendspin_bridge.sync_tuning.ma_api import MaApi, MaApiError
from sendspin_sync.models import Member, Snapshot
from sendspin_sync.ports import ControlError

DELAY_KEY = "sendspin_static_delay"; SPLIT = "||protocol||"


def _bridge_ids() -> set[str]:
    return set(get_ma_player_ids().values())


def _wrap(exc: MaApiError) -> ControlError:
    return ControlError(exc.code, exc.detail)


class SignalStrategy(Protocol):
    def start(self, group_id: str, url: str) -> None: ...
    def stop(self, group_id: str) -> None: ...


class SoloStrategy(Protocol):
    def solo(self, group_id: str, members: list[Member], member: str | None) -> None: ...


class AnnouncementSignal:
    """Option (a): Music Assistant plays the clip as an announcement on the group; reply not awaited."""
    def __init__(self, api: MaApi, volume_level: int | None = None) -> None: self._api, self._volume = api, volume_level
    def start(self, group_id: str, url: str) -> None:
        args: dict[str, Any] = {"player_id": group_id, "url": url, "pre_announce": False}
        if self._volume is not None: args["volume_level"] = self._volume
        self._api.fire("players/cmd/play_announcement", args)
    def stop(self, group_id: str) -> None:
        self._api.call("players/cmd/stop", {"player_id": group_id})


class QueueSignal:
    """Option (b): play the clip through the group's queue; the snapshot token restores the queue."""
    def __init__(self, api: MaApi) -> None: self._api = api; self._saved: dict[str, dict] = {}
    def start(self, group_id: str, url: str) -> None:
        q = self._api.call("player_queues/get", {"queue_id": group_id}) or {}
        items = self._api.call("player_queues/items", {"queue_id": group_id, "limit": 500, "offset": 0}) or []
        self._saved[group_id] = {"uris": [i.get("uri") or i.get("media_item", {}).get("uri") for i in items],
                                 "index": q.get("current_index"), "position": q.get("elapsed_time"),
                                 "shuffle": q.get("shuffle_enabled"), "repeat": q.get("repeat_mode")}
        self._api.call("player_queues/play_media", {"queue_id": group_id, "media": url, "option": "replace"})
    def stop(self, group_id: str) -> None:
        saved = self._saved.pop(group_id, None)
        self._api.call("players/cmd/stop", {"player_id": group_id})
        if not saved or not saved["uris"]: return
        self._api.call("player_queues/play_media", {"queue_id": group_id, "media": [u for u in saved["uris"] if u], "option": "replace"}, timeout=30.0)
        if saved["index"] is not None: self._api.call("player_queues/play_index", {"queue_id": group_id, "index": saved["index"]})
        if saved["position"]: self._api.call("player_queues/seek", {"queue_id": group_id, "position": int(saved["position"])})
        if saved["shuffle"] is not None: self._api.call("player_queues/shuffle", {"queue_id": group_id, "shuffle_enabled": saved["shuffle"]})
        if saved["repeat"] is not None: self._api.call("player_queues/repeat", {"queue_id": group_id, "repeat_mode": saved["repeat"]})


class MaMuteSolo:
    """Solo = mute every other member through Music Assistant; remembers what it muted."""
    def __init__(self, api: MaApi) -> None: self._api = api; self._muted_by_us: dict[str, set[str]] = {}
    def solo(self, group_id: str, members: list[Member], member: str | None) -> None:
        ours = self._muted_by_us.setdefault(group_id, set())
        states = {p["player_id"]: p for p in self._api.call("players/all", {"return_protocol_players": True})}
        for m in members:
            want_muted = member is not None and m.ma_player_id != member
            was_muted = bool(states.get(m.ma_player_id, {}).get("volume_muted"))
            if want_muted and not was_muted:
                self._api.call("players/cmd/volume_mute", {"player_id": m.ma_player_id, "muted": True}); ours.add(m.ma_player_id)
            elif not want_muted and m.ma_player_id in ours:
                self._api.call("players/cmd/volume_mute", {"player_id": m.ma_player_id, "muted": False}); ours.discard(m.ma_player_id)


class HybridSolo(MaMuteSolo):
    """Option (c): bridge members are muted locally (no round trip), others through Music Assistant."""
    def __init__(self, api: MaApi, bridge_ids: Callable[[], set[str]] = _bridge_ids) -> None:
        super().__init__(api); self._bridge_ids = bridge_ids
    def solo(self, group_id: str, members: list[Member], member: str | None) -> None:
        from sendspin_bridge.application.playback import set_device_mute
        local = {m.ma_player_id for m in members if m.ma_player_id in self._bridge_ids()}
        for mid in local:
            set_device_mute(mid, member is not None and mid != member)
        super().solo(group_id, [m for m in members if m.ma_player_id not in local], member)


class MaGroupControl:
    def __init__(self, api: MaApi, *, signal: SignalStrategy, solo: SoloStrategy, groups: Callable[[], list[dict]] = get_ma_groups,
                 bridge_player_ids: Callable[[], set[str]] = _bridge_ids) -> None:
        self._api, self._signal, self._solo, self._groups, self._bridge_ids = api, signal, solo, groups, bridge_player_ids
        self._members_cache: dict[str, list[Member]] = {}

    def _group(self, group_id: str) -> dict:
        for g in self._groups():
            if g.get("id") == group_id: return g
        raise ControlError("member_gone", f"group {group_id} is not known")

    def _states(self) -> dict[str, dict]:
        try: return {p["player_id"]: p for p in self._api.call("players/all", {"return_protocol_players": True})}
        except MaApiError as exc: raise _wrap(exc) from exc

    def members(self, group_id: str) -> list[Member]:
        group, states, bridge_ids, out = self._group(group_id), self._states(), self._bridge_ids(), []
        for entry in group.get("members") or []:
            mid = str(entry.get("id")); st = states.get(mid, {}); name = st.get("name") or entry.get("name") or mid
            client_id: str | None = None
            if st.get("provider") == "sendspin": client_id = mid
            else:
                for proto in st.get("output_protocols") or []:
                    if proto.get("protocol_domain") == "sendspin": client_id = str(proto.get("output_protocol_id")); break
            origin = "other" if client_id is None else ("bridge" if mid in bridge_ids else "sendspin")
            adjustable, delay = False, None
            if client_id is not None:
                try:
                    entries = self._api.call("config/players/get_entries", {"player_id": client_id}) or []
                    adjustable = any(e.get("key") == DELAY_KEY for e in entries)
                    if adjustable:
                        delay = int(self._api.call("config/players/get_value", {"player_id": client_id, "key": DELAY_KEY, "default": 0}) or 0)
                except MaApiError as exc: raise _wrap(exc) from exc
            out.append(Member(ma_player_id=mid, name=str(name), adjustable=adjustable, origin=origin, client_id=client_id, delay_ms=delay))
        self._members_cache[group_id] = out
        return out

    def delays(self, members):
        return {m.ma_player_id: m.delay_ms for m in members}

    def set_delay(self, member: Member, ms: int) -> None:
        if not member.adjustable or member.client_id is None: raise ControlError("not_adjustable", member.name)
        key = DELAY_KEY if member.client_id == member.ma_player_id else f"{member.client_id}{SPLIT}{DELAY_KEY}"
        try: self._api.call("config/players/save", {"player_id": member.ma_player_id, "values": {key: int(ms)}})
        except MaApiError as exc: raise _wrap(exc) from exc

    def snapshot(self, group_id: str) -> Snapshot:
        states = self._states(); members = self._members_cache.get(group_id) or self.members(group_id)
        ids = [m.ma_player_id for m in members]
        return Snapshot(group_id=group_id, delays={m.ma_player_id: m.delay_ms or 0 for m in members},
                        volumes={i: states.get(i, {}).get("volume_level") for i in ids},
                        muted={i: bool(states.get(i, {}).get("volume_muted")) for i in ids},
                        playing=states.get(group_id, {}).get("playback_state") == "playing")

    def start_signal(self, group_id: str, url: str) -> None:
        try: self._signal.start(group_id, url)
        except MaApiError as exc: raise _wrap(exc) from exc
    def stop_signal(self, group_id: str) -> None:
        try: self._signal.stop(group_id)
        except MaApiError as exc: raise _wrap(exc) from exc
    def solo(self, group_id: str, member: str | None) -> None:
        members = self._members_cache.get(group_id) or self.members(group_id)
        try: self._solo.solo(group_id, members, member)
        except MaApiError as exc: raise _wrap(exc) from exc

    def restore(self, snapshot: Snapshot) -> None:
        """Re-assert the mute and volume the snapshot recorded; delays are never touched."""
        try:
            states = self._states()
            for mid, muted in snapshot.muted.items():
                if bool(states.get(mid, {}).get("volume_muted")) != muted:
                    self._api.call("players/cmd/volume_mute", {"player_id": mid, "muted": muted})
            for mid, vol in snapshot.volumes.items():
                if vol is not None and states.get(mid, {}).get("volume_level") not in (None, vol):
                    self._api.call("players/cmd/volume_set", {"player_id": mid, "volume_level": vol})
        except MaApiError as exc: raise _wrap(exc) from exc


def default_group_control() -> MaGroupControl:
    from sendspin_bridge.sync_tuning.ma_api import ma_api_from_config
    api = ma_api_from_config()
    # Stage 0 decision: (a) AnnouncementSignal + MaMuteSolo | (c) AnnouncementSignal + HybridSolo | (b) QueueSignal + MaMuteSolo
    return MaGroupControl(api, signal=AnnouncementSignal(api), solo=MaMuteSolo(api))
```

For (c) add to the tests: `HybridSolo` with `monkeypatch.setattr("sendspin_bridge.application.playback.set_device_mute", recorder)` asserting the bridge member is muted locally and `players/cmd/volume_mute` is never sent for it. For (b) add: `QueueSignal.start` saves `player_queues/get`+`items` and `stop` replays `play_media`/`play_index`/`seek` with the saved values (extend `FakeMa.call` with those commands returning `{"current_index": 3, "elapsed_time": 42, "shuffle_enabled": False, "repeat_mode": "off"}` and three items).

- [ ] **Step 5: Run** — Expected: PASS. Gates (mypy may need `# type: ignore[attr-defined]` on `MusicAssistantClient.__aenter__`; prefer `async with` restructuring over ignores).

- [ ] **Step 6: Commit** `git add src/sendspin_bridge/sync_tuning tests/unit/sync_tuning && git commit -m "feat(sync): Music Assistant group control adapter"`.

---

### Task 2.4: `SessionStore`, persisted snapshot, restore after restart, `sync` events

**Files:**
- Create: `src/sendspin_bridge/sync_tuning/session_store.py`, `tests/unit/sync_tuning/test_session_store.py`
- Modify: `src/sendspin_bridge/api/events.py` (`ALL_TYPES`, `DEFAULT_TYPES`, module docstring), `src/sendspin_bridge/bridge/orchestrator.py` (call `session_store.restore_persisted()` after MA integration starts; `session_store.shutdown()` in graceful shutdown), `tests/unit/api/test_events.py` (one assertion)

**Interfaces:**
- Consumes: `SyncSession`, `Snapshot`, `snapshot_from_dict`, `to_dict`; `GroupControl`, `ClickTrackHost`; `CONFIG_DIR` from `sendspin_bridge.config`; `EventHub.publish_threadsafe` + `envelope` via an injected `publish: Callable[[dict], None]` (the store does not import FastAPI).
- Produces:

```python
SNAPSHOT_FILE = "sync_snapshot.json"; RESTORE_BACKOFF_S = (1, 2, 5, 10, 30); 
class SessionStore:
    def __init__(self, *, control_factory: Callable[[], GroupControl], host: ClickTrackHost,
                 publish: Callable[[dict], None], snapshot_dir: Callable[[], Path], clock=time.monotonic) -> None
    def create(self, group_id: str, mode: Mode, point: Point) -> SyncSession   # ControlError("session_active") if one is live for the group
    def get(self, session_id: str) -> SyncSession                               # ControlError("session_expired") when unknown/finished > 60 s
    def active_for(self, group_id: str) -> SyncSession | None
    def end(self, session_id: str) -> None                                       # cancel → restoring; store drives restore_tick with backoff
    def tick(self) -> None                                                       # expiry check + restore attempts (called by a 1 s timer thread)
    def restore_persisted(self) -> None                                          # on boot: read snapshot file → stop signal, restore, delete file
    def shutdown(self) -> None
    def publish_change(self, session: SyncSession) -> None                      # envelope("sync", session.to_dict())
store: SessionStore  # module singleton, built lazily by `get_store()` with default_group_control, FastApiClickTrackHost, hub publish
```

Persistence: on `create`, `{"session_id", "group_id", "snapshot": to_dict(snapshot), "signal_running", "soloed", "muted_by_us"}` is written to `CONFIG_DIR / "sync_snapshot.json"`; rewritten on every `on_change`; deleted when `restore_tick()` returns True.

- [ ] **Step 1: Failing tests**

```python
import json, pytest
from pathlib import Path
from sendspin_bridge.sync_tuning.session_store import SessionStore
from sendspin_sync.ports import ControlError
from tests.unit.sendspin_sync.test_session import FakeControl, FakeHost   # reuse the fakes


def make(tmp_path, clock=None):
    ctl = FakeControl(); events = []; t = {"now": 0.0}
    store = SessionStore(control_factory=lambda: ctl, host=FakeHost(), publish=events.append,
                         snapshot_dir=lambda: tmp_path, clock=clock or (lambda: t["now"]))
    return store, ctl, events, t


def test_one_session_per_group_and_events_are_published(tmp_path):
    store, ctl, events, _ = make(tmp_path)
    s = store.create("g", "semi", "device")
    assert s.state == "measuring" and events[-1]["type"] == "sync" and events[-1]["data"]["id"] == s.id
    with pytest.raises(ControlError) as exc: store.create("g", "manual", "device")
    assert exc.value.code == "session_active"
    assert json.loads((tmp_path / "sync_snapshot.json").read_text())["group_id"] == "g"


def test_end_restores_with_backoff_and_removes_the_snapshot(tmp_path):
    store, ctl, _, t = make(tmp_path); s = store.create("g", "semi", "device"); ctl.fail_restore = 2
    store.end(s.id)
    assert s.state == "restoring" and (tmp_path / "sync_snapshot.json").exists()
    store.tick(); assert s.state == "restoring"            # attempt 1 failed, next after 1 s
    t["now"] = 0.5; store.tick(); assert ("restore",) not in ctl.calls
    t["now"] = 1.1; store.tick(); t["now"] = 3.2; store.tick()
    assert s.state == "cancelled" and not (tmp_path / "sync_snapshot.json").exists()
    t["now"] = 70.0; store.tick()
    with pytest.raises(ControlError) as exc: store.get(s.id)
    assert exc.value.code == "session_expired"


def test_inactivity_ends_the_session(tmp_path):
    store, ctl, _, t = make(tmp_path); s = store.create("g", "manual", "device")
    t["now"] = 901.0; store.tick(); store.tick()
    assert s.state == "cancelled" and ("restore",) in ctl.calls


def test_restore_persisted_after_restart_stops_signal_and_unmutes(tmp_path):
    (tmp_path / "sync_snapshot.json").write_text(json.dumps({
        "session_id": "old", "group_id": "g", "signal_running": True, "soloed": "a",
        "snapshot": {"group_id": "g", "delays": {"a": 0}, "volumes": {"a": 40}, "muted": {"a": False, "b": False}, "playing": True, "restore_token": {}}}))
    store, ctl, _, _ = make(tmp_path)
    store.restore_persisted()
    assert ctl.calls == [("solo", None), ("stop",), ("restore",)] and not (tmp_path / "sync_snapshot.json").exists()
    with pytest.raises(ControlError): store.get("old")


def test_restore_persisted_keeps_the_file_when_ma_is_down(tmp_path):
    (tmp_path / "sync_snapshot.json").write_text(json.dumps({"session_id": "old", "group_id": "g", "signal_running": False, "soloed": None,
        "snapshot": {"group_id": "g", "delays": {}, "volumes": {}, "muted": {}, "playing": False, "restore_token": {}}}))
    store, ctl, _, t = make(tmp_path); ctl.fail_restore = 1
    store.restore_persisted()
    assert (tmp_path / "sync_snapshot.json").exists()
    t["now"] = 2.0; store.tick()
    assert not (tmp_path / "sync_snapshot.json").exists()
```

In `tests/unit/api/test_events.py` add:

```python
def test_sync_is_a_known_default_event_type():
    assert "sync" in events.ALL_TYPES and "sync" in events.DEFAULT_TYPES
    assert events._parse_types("sync") == frozenset({"sync"})
```

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement** `session_store.py`:

```python
"""Sessions by id, one per group; restore with backoff; the snapshot survives a restart."""
from __future__ import annotations
import json, logging, threading, time, uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from sendspin_sync.models import Snapshot, snapshot_from_dict, to_dict
from sendspin_sync.ports import ClickTrackHost, ControlError, GroupControl
from sendspin_sync.session import Mode, Point, SyncSession

logger = logging.getLogger(__name__)
SNAPSHOT_FILE = "sync_snapshot.json"; RESTORE_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0); FINISHED_TTL_S = 60.0


class SessionStore:
    def __init__(self, *, control_factory, host: ClickTrackHost, publish: Callable[[dict[str, Any]], None],
                 snapshot_dir: Callable[[], Path], clock: Callable[[], float] = time.monotonic) -> None:
        self._factory, self._host, self._publish, self._dir, self._clock = control_factory, host, publish, snapshot_dir, clock
        self._lock = threading.RLock(); self._sessions: dict[str, SyncSession] = {}
        self._next_restore: dict[str, float] = {}; self._attempts: dict[str, int] = {}; self._finished_at: dict[str, float] = {}
        self._pending: dict[str, Any] | None = None   # a persisted snapshot still waiting for MA

    def _path(self) -> Path: return self._dir() / SNAPSHOT_FILE

    def publish_change(self, session: SyncSession) -> None:
        from datetime import UTC, datetime
        self._write_snapshot(session)
        self._publish({"v": 1, "type": "sync", "at": datetime.now(tz=UTC).isoformat(), "data": session.to_dict()})

    def _write_snapshot(self, session: SyncSession) -> None:
        if session.state in ("done", "cancelled", "failed") or session.snapshot is None: return
        data = {"session_id": session.id, "group_id": session.group_id, "signal_running": session.signal_running,
                "soloed": session.soloed, "snapshot": to_dict(session.snapshot)}
        tmp = self._path().with_suffix(".tmp"); tmp.write_text(json.dumps(data)); tmp.replace(self._path())

    def active_for(self, group_id: str) -> SyncSession | None:
        with self._lock:
            return next((s for s in self._sessions.values() if s.group_id == group_id and s.state not in ("done", "cancelled", "failed")), None)

    def create(self, group_id: str, mode: Mode, point: Point) -> SyncSession:
        with self._lock:
            if self.active_for(group_id) is not None: raise ControlError("session_active", "tuning in progress for this group")
            session = SyncSession(session_id=uuid.uuid4().hex, group_id=group_id, mode=mode, point=point, control=self._factory(),
                                  host=self._host, clock=self._clock, on_change=self.publish_change)
            self._sessions[session.id] = session
            try: session.start()
            except ControlError:
                self._sessions.pop(session.id, None); raise
            return session

    def get(self, session_id: str) -> SyncSession:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None: raise ControlError("session_expired", "no such session")
            return s

    def end(self, session_id: str) -> None:
        s = self.get(session_id); s.cancel(); self._schedule(s, 0.0)

    def _schedule(self, s: SyncSession, delay: float) -> None:
        self._next_restore[s.id] = self._clock() + delay

    def tick(self) -> None:
        with self._lock:
            now = self._clock()
            for s in list(self._sessions.values()):
                if s.state not in ("restoring", "done", "cancelled", "failed") and s.expired():
                    s.cancel(); self._schedule(s, 0.0)
                if s.state == "restoring" and now >= self._next_restore.get(s.id, 0.0):
                    if s.restore_tick():
                        self._finished_at[s.id] = now; self._attempts.pop(s.id, None); self._next_restore.pop(s.id, None)
                        self._path().unlink(missing_ok=True); self.publish_change(s)
                    else:
                        n = self._attempts.get(s.id, 0); self._attempts[s.id] = n + 1
                        self._schedule(s, RESTORE_BACKOFF_S[min(n, len(RESTORE_BACKOFF_S) - 1)])
                if s.state in ("done", "cancelled", "failed") and now - self._finished_at.get(s.id, now) > FINISHED_TTL_S:
                    self._sessions.pop(s.id, None); self._finished_at.pop(s.id, None)
            if self._pending is not None and now >= self._next_restore.get("__pending__", 0.0):
                self._try_pending()

    def restore_persisted(self) -> None:
        path = self._path()
        if not path.exists(): return
        try: self._pending = json.loads(path.read_text())
        except (OSError, ValueError):
            logger.warning("Unreadable %s; removing", path); path.unlink(missing_ok=True); return
        self._try_pending()

    def _try_pending(self) -> None:
        assert self._pending is not None
        data, ctl = self._pending, self._factory()
        try:
            group = str(data["group_id"])
            if data.get("soloed") is not None: ctl.solo(group, None)
            if data.get("signal_running"): ctl.stop_signal(group)
            ctl.restore(snapshot_from_dict(data["snapshot"]))
        except (ControlError, KeyError) as exc:
            n = self._attempts.get("__pending__", 0); self._attempts["__pending__"] = n + 1
            self._next_restore["__pending__"] = self._clock() + RESTORE_BACKOFF_S[min(n, len(RESTORE_BACKOFF_S) - 1)]
            logger.warning("Sync restore after restart failed (%s); retrying", exc); return
        self._pending = None; self._path().unlink(missing_ok=True)
        logger.info("Sync session %s restored after restart", data.get("session_id"))

    def shutdown(self) -> None:
        with self._lock:
            for s in self._sessions.values():
                if s.state not in ("done", "cancelled", "failed"): s.cancel()
            for s in self._sessions.values():
                if s.state == "restoring": s.restore_tick()


_store: SessionStore | None = None; _ticker: threading.Thread | None = None


def get_store() -> SessionStore:
    global _store, _ticker
    if _store is None:
        from sendspin_bridge import config as cfg
        from sendspin_bridge.api.events import envelope
        from sendspin_bridge.sync_tuning.click_track_host import FastApiClickTrackHost
        from sendspin_bridge.sync_tuning.ma_group_control import default_group_control

        def publish(event: dict[str, Any]) -> None:
            from sendspin_bridge.bridge import state
            hub = getattr(state, "event_hub", None)
            if hub is not None: hub.publish_threadsafe(envelope("sync", event["data"]))

        _store = SessionStore(control_factory=default_group_control, host=FastApiClickTrackHost(), publish=publish, snapshot_dir=lambda: cfg.CONFIG_DIR)

        def run() -> None:
            while True:
                time.sleep(1.0)
                try: _store.tick()
                except Exception: logger.exception("sync session tick failed")
        _ticker = threading.Thread(target=run, name="sync-session-tick", daemon=True); _ticker.start()
    return _store
```

Wire `publish`: in `api/app.py` the hub lives on `app.state.event_hub`; expose it to the store by setting `state.event_hub = app.state.event_hub` in `create_app` (one line in `sendspin_bridge/bridge/state.py`: `event_hub = None`). `events.py`: `DEFAULT_TYPES = frozenset({"status", "job", "sync"})`, `ALL_TYPES = frozenset({"status", "job", "log", "sync"})`, docstring bullet `* ``sync`` — a group sync-tuning session whenever its state changes.` `orchestrator.py`: after MA integration, `get_store().restore_persisted()` inside `try/except Exception: logger.exception(...)`; in `graceful_shutdown()` call `get_store().shutdown()` guarded the same way.

- [ ] **Step 4: Run** `uv run pytest -q -p no:cacheprovider tests/unit/sync_tuning tests/unit/api/test_events.py` — Expected: PASS. Gates.

- [ ] **Step 5: Commit** `git add -A src/sendspin_bridge tests && git commit -m "feat(sync): session store with persisted snapshot, restore after restart and sync events"`.

---

### Task 2.5: HTTP layer `/api/v1/sync/*`, models, contract export, CHANGELOG

**Files:**
- Create: `src/sendspin_bridge/application/models/sync.py`, `src/sendspin_bridge/application/sync_tuning.py`, `scripts/export_sync_contract.py`, `docs/contracts/sync-contract-v1.json`, `tests/unit/api/test_sync_api.py`, `tests/unit/application/test_sync_contract.py`
- Modify: `src/sendspin_bridge/api/routers/sync.py` (add `router`), `src/sendspin_bridge/api/app.py` (`v1.include_router(sync.router)`), `ui/openapi.json` (regenerated), `ui/src/api/schema.d.ts` (regenerated), `CHANGELOG.md`

**Interfaces:**
- Consumes: `get_store()`, `SyncSession`, `ConfigProfileRepository`, `default_group_control()`, `planner`, `Recording`.
- Produces — Pydantic (`application/models/sync.py`):

```python
class SyncMember(BaseModel): ma_player_id: str; name: str; origin: Literal["bridge","sendspin","other"]; adjustable: bool; client_id: str | None = None; delay_ms: int | None = None; last_measured_at: str | None = None
class SyncGroup(BaseModel): id: str; name: str; members: list[SyncMember]; session_id: str | None = None
class MeasurementOut(BaseModel): member: str; status: Literal["ok","not_heard","unstable","skipped"]; phase_ms: float | None = None; confidence: float = 0.0; clicks: int = 0; spread_ms: float | None = None; reason: str = ""
class ProposalRowOut(BaseModel): member: str; before_ms: int; after_ms: int; delta_ms: int; clamped: bool
class ProposalOut(BaseModel): reference: str; rows: list[ProposalRowOut]; predicted_spread_ms: float; unverified: list[str]; warnings: list[str]
class SyncSessionOut(BaseModel): id: str; group_id: str; mode: Literal["manual","semi","auto"]; point: Literal["device","position"]; state: str; members: list[SyncMember]; measurements: dict[str, MeasurementOut]; proposal: ProposalOut | None; history: list[dict[str, Any]]; error: str | None; signal_running: bool; soloed: str | None; reference: str | None; round: int; undo_until_s: float | None
class SessionCreate(BaseModel): group_id: str; mode: Literal["manual","semi","auto"]; point: Literal["device","position"] = "device"
class SignalIn(BaseModel): action: Literal["start","stop"]
class SoloIn(BaseModel): member: str | None
class AbIn(BaseModel): a: str; b: str
class ReferenceIn(BaseModel): member: str
class ApplyIn(BaseModel): rows: dict[str, int] | None = None
class DelaysIn(BaseModel): delays: dict[str, int]
class ProfileOut(BaseModel): id: str; name: str; group_id: str; kind: Literal["device","position"]; delays: dict[str, int]; created_at: str; notes: str = ""
class ProfileCreate(BaseModel): group_id: str; name: str; kind: Literal["device","position"] = "device"; delays: dict[str, int]; notes: str = ""
class ProfilePatch(BaseModel): name: str | None = None; notes: str | None = None
class CaptureIn(BaseModel): name: str; kind: Literal["device","position"] = "device"
class ProfileApplyOut(BaseModel): applied: dict[str, int]; skipped: list[str]
```

Use cases (`application/sync_tuning.py`), all synchronous, raising `UseCaseError` mapped from `ControlError` with `_STATUS = {"ma_unavailable": 503, "ma_forbidden": 403, "session_active": 409, "session_expired": 404, "member_gone": 409, "not_adjustable": 409, "recording_invalid": 400, "restore_pending": 409, "undo_expired": 409, "session_state": 409, "no_measurement": 409}` (default 502):

```python
def list_groups() -> list[SyncGroup]; def get_group(group_id) -> SyncGroup
def write_delays(group_id, delays: dict[str, int]) -> SyncGroup          # manual write without a session (validates adjustable, 0..5000)
def create_session(body: SessionCreate) -> SyncSessionOut; def get_session(sid) -> SyncSessionOut; def end_session(sid) -> None
def signal(sid, action) -> SyncSessionOut; def solo(sid, member) -> SyncSessionOut; def ab(sid, a, b) -> SyncSessionOut; def set_reference(sid, member) -> SyncSessionOut
def upload_recording(sid, member, start_time_s, sample_rate, body: bytes) -> MeasurementOut   # size ≤ 2 MB, even length, else 400 recording_invalid; estimator "invalid_recording" → 400
def skip(sid, member) -> SyncSessionOut; def proposal(sid) -> ProposalOut; def apply(sid, rows) -> SyncSessionOut; def undo(sid) -> SyncSessionOut; def verify(sid) -> SyncSessionOut
def session_delay(sid, member, ms) -> SyncSessionOut                      # manual mode live write
def list_profiles(group_id) -> list[ProfileOut]; def create_profile(body) -> ProfileOut; def patch_profile(pid, body) -> ProfileOut; def delete_profile(pid) -> None
def apply_profile(pid) -> ProfileApplyOut; def capture_profile(group_id, body: CaptureIn) -> ProfileOut
```

Routes (`router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)`): exactly the spec's list — `GET /groups`, `GET /groups/{group_id}`, `PUT /groups/{group_id}/delays`, `POST /groups/{group_id}/capture`, `POST /sessions` (201), `GET|DELETE /sessions/{id}` (DELETE 204), `POST /sessions/{id}/signal|solo|ab|reference|skip|apply|undo|verify`, `PUT /sessions/{id}/delays/{member}` `{ms}`, `POST /sessions/{id}/recordings?member=&start_time_s=&sample_rate=` with `body: bytes = Body(media_type="application/octet-stream")`, `GET /sessions/{id}/proposal`, `GET|POST /profiles?group_id=`, `PATCH|DELETE /profiles/{id}`, `POST /profiles/{id}/apply`.

- [ ] **Step 1: Failing tests** (`tests/unit/api/test_sync_api.py`; monkeypatch the store and control):

```python
import struct, pytest
from sendspin_bridge.sync_tuning import session_store
from sendspin_bridge.sync_tuning.session_store import SessionStore
from tests.unit.sendspin_sync.synth import synth
from tests.unit.sendspin_sync.test_session import FakeControl, FakeHost
from tests.unit.sync_tuning.test_ma_group_control import GROUPS


@pytest.fixture
def sync_env(monkeypatch, tmp_config, tmp_path):
    ctl = FakeControl(); events = []
    store = SessionStore(control_factory=lambda: ctl, host=FakeHost(), publish=events.append, snapshot_dir=lambda: tmp_path)
    monkeypatch.setattr(session_store, "_store", store)
    monkeypatch.setattr("sendspin_bridge.application.sync_tuning._control", lambda: ctl)
    monkeypatch.setattr("sendspin_bridge.application.sync_tuning.get_ma_groups", lambda: [{"id": "g", "name": "Downstairs", "members": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "cast", "name": "Cast"}]}])
    return ctl, store, events


def test_groups_list_members_with_origin_and_adjustability(api_client, sync_env):
    resp = api_client.get("/api/v1/sync/groups"); assert resp.status_code == 200
    g = resp.json()[0]; assert g["id"] == "g" and g["session_id"] is None
    assert {m["ma_player_id"]: m["adjustable"] for m in g["members"]} == {"a": True, "b": True, "cast": False}


def test_manual_delay_write_refuses_a_non_adjustable_member(api_client, sync_env):
    ok = api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"b": 250}})
    assert ok.status_code == 200 and sync_env[0].delays_now["b"] == 250
    bad = api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"cast": 10}})
    assert bad.status_code == 409 and bad.json()["code"] == "not_adjustable"
    assert api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"a": 6000}}).status_code == 422


def test_session_flow_over_http(api_client, sync_env):
    ctl, store, events = sync_env
    created = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"})
    assert created.status_code == 201; sid = created.json()["id"]; assert created.json()["state"] == "measuring"
    assert api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"}).json()["code"] == "session_active"
    for member, lag in (("a", 0.0), ("b", 0.03)):
        rec = synth(member, lag_s=lag)
        resp = api_client.post(f"/api/v1/sync/sessions/{sid}/recordings", params={"member": member, "start_time_s": rec.start_time_s, "sample_rate": rec.sample_rate},
                               content=rec.pcm, headers={"content-type": "application/octet-stream"})
        assert resp.status_code == 200 and resp.json()["status"] == "ok"
    prop = api_client.get(f"/api/v1/sync/sessions/{sid}/proposal").json()
    assert {r["member"]: r["after_ms"] for r in prop["rows"]} == {"a": 0, "b": 30}
    assert api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={}).json()["state"] == "restoring"
    assert events and events[-1]["type"] == "sync"
    assert api_client.delete(f"/api/v1/sync/sessions/{sid}").status_code == 204


def test_garbage_recordings_are_400_not_500(api_client, sync_env):
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
    url = f"/api/v1/sync/sessions/{sid}/recordings"
    for params, body in (({"member": "a", "start_time_s": 0, "sample_rate": 7000}, bytes(32000)),
                         ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, bytes(4801)),
                         ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, bytes(2_100_000)),
                         ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, bytes(600))):
        resp = api_client.post(url, params=params, content=body, headers={"content-type": "application/octet-stream"})
        assert resp.status_code == 400 and resp.json()["code"] == "recording_invalid", params
    silent = api_client.post(url, params={"member": "a", "start_time_s": 0, "sample_rate": 16000}, content=bytes(6 * 16000 * 2), headers={"content-type": "application/octet-stream"})
    assert silent.status_code == 200 and silent.json()["status"] == "not_heard"
    gone = api_client.post(url, params={"member": "zz", "start_time_s": 0, "sample_rate": 16000}, content=bytes(6 * 16000 * 2), headers={"content-type": "application/octet-stream"})
    assert gone.status_code == 409 and gone.json()["code"] == "member_gone"


def test_forbidden_apply_keeps_the_proposal(api_client, sync_env):
    from sendspin_sync.ports import ControlError
    ctl, _, _ = sync_env
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
    for member, lag in (("a", 0.0), ("b", 0.03)):
        rec = synth(member, lag_s=lag)
        api_client.post(f"/api/v1/sync/sessions/{sid}/recordings", params={"member": member, "start_time_s": rec.start_time_s, "sample_rate": rec.sample_rate}, content=rec.pcm, headers={"content-type": "application/octet-stream"})
    api_client.get(f"/api/v1/sync/sessions/{sid}/proposal")
    def boom(member, ms): raise ControlError("ma_forbidden", "scope")
    ctl.set_delay = boom
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={})
    assert resp.status_code == 403 and resp.json()["code"] == "ma_forbidden"
    again = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert again["state"] == "proposed" and again["proposal"]["rows"]


def test_profiles_crud_apply_and_capture(api_client, sync_env):
    ctl, _, _ = sync_env; ctl.delays_now = {"a": 120, "b": 0}
    cap = api_client.post("/api/v1/sync/groups/g/capture", json={"name": "Evening"})
    assert cap.status_code == 201 and cap.json()["delays"] == {"a": 120, "b": 0}
    pid = cap.json()["id"]
    assert [p["name"] for p in api_client.get("/api/v1/sync/profiles", params={"group_id": "g"}).json()] == ["Evening"]
    assert api_client.patch(f"/api/v1/sync/profiles/{pid}", json={"name": "Night"}).json()["name"] == "Night"
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "b"]; ctl.delays_now = {"a": 0}
    applied = api_client.post(f"/api/v1/sync/profiles/{pid}/apply").json()
    assert applied == {"applied": {"a": 120}, "skipped": ["b"]} and ctl.delays_now["a"] == 120
    assert api_client.delete(f"/api/v1/sync/profiles/{pid}").status_code == 204
    assert api_client.get(f"/api/v1/sync/profiles", params={"group_id": "g"}).json() == []


def test_sync_routes_need_credentials_when_auth_is_on():
    from tests.support.api_client import make_client
    assert make_client({"AUTH_ENABLED": True}).get("/api/v1/sync/groups").status_code == 401
```

`tests/unit/application/test_sync_contract.py`:

```python
import subprocess, sys
from pathlib import Path

def test_contract_file_is_current():
    root = Path(__file__).resolve().parents[3]
    proc = subprocess.run([sys.executable, str(root / "scripts" / "export_sync_contract.py"), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Run** `uv run pytest -q -p no:cacheprovider tests/unit/api/test_sync_api.py tests/unit/application/test_sync_contract.py` — Expected: FAIL (404s / missing script).

- [ ] **Step 3: Implement** the models, the use cases and the router. Key fragments:

```python
# application/sync_tuning.py
_STATUS = {"ma_unavailable": 503, "ma_forbidden": 403, "session_active": 409, "session_expired": 404, "member_gone": 409,
           "not_adjustable": 409, "recording_invalid": 400, "restore_pending": 409, "undo_expired": 409, "session_state": 409,
           "no_measurement": 409, "not_found": 404}

def _raise(exc: ControlError) -> NoReturn:
    raise UseCaseError(_STATUS.get(exc.code, 502), exc.code, exc.detail or exc.code) from exc

def _control():                       # patched in tests
    from sendspin_bridge.sync_tuning.ma_group_control import default_group_control
    return default_group_control()

def _members(group_id: str) -> list[Member]:
    try: return _control().members(group_id)
    except ControlError as exc: _raise(exc)
    except MaApiError as exc: raise UseCaseError(503 if exc.code == "ma_unavailable" else 502, exc.code, exc.detail)

def list_groups() -> list[SyncGroup]:
    store = get_store(); out = []
    for g in get_ma_groups():
        active = store.active_for(g["id"])
        members = active.members if active else _members(g["id"])
        out.append(SyncGroup(id=g["id"], name=g["name"], members=[SyncMember(**to_dict(m)) for m in members], session_id=active.id if active else None))
    return out

MAX_RECORDING_BYTES = 2_000_000

def upload_recording(sid: str, member: str, start_time_s: float, sample_rate: int, body: bytes) -> MeasurementOut:
    if not body or len(body) > MAX_RECORDING_BYTES or len(body) % 2 or not 12000 <= sample_rate <= 48000:
        raise UseCaseError(400, "recording_invalid", "Unsupported recording size or sample rate")
    session = _session(sid)
    try: m = session.record(Recording(member=member, start_time_s=float(start_time_s), sample_rate=int(sample_rate), pcm=body))
    except ControlError as exc: _raise(exc)
    if m.reason == "invalid_recording": raise UseCaseError(400, "recording_invalid", "Recording too short or too long")
    return MeasurementOut(**to_dict(m))

def apply_profile(pid: str) -> ProfileApplyOut:
    p = _repo().get(pid)
    if p is None: raise UseCaseError(404, "not_found", "No such profile")
    members = {m.ma_player_id: m for m in _members(p.group_id)}
    applied, skipped = {}, []
    for mid, ms in p.delays.items():
        m = members.get(mid)
        if m is None or not m.adjustable: skipped.append(mid); continue
        try: _control().set_delay(m, ms); applied[mid] = ms
        except ControlError as exc: _raise(exc)
    return ProfileApplyOut(applied=applied, skipped=skipped)
```

```python
# api/routers/sync.py (router part)
router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)

@router.post("/sessions/{session_id}/recordings", response_model=MeasurementOut, summary="Upload one member's recording")
def recordings(session_id: str, member: str, start_time_s: float, sample_rate: int,
               body: bytes = Body(media_type="application/octet-stream")) -> MeasurementOut:
    return uc.upload_recording(session_id, member, start_time_s, sample_rate, body)
```

`scripts/export_sync_contract.py`: like `export_openapi.py` — builds `{"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "sync-contract v1", "$defs": {name: model.model_json_schema(ref_template="#/$defs/{model}") for every model above}}` sorted, writes `docs/contracts/sync-contract-v1.json`, `--check` exits 1 when stale.

- [ ] **Step 4: Run** the two test files — Expected: PASS. Then `uv run python scripts/export_openapi.py && (cd ui && npm run gen:api)` and the full gates. Commit `ui/openapi.json`, `ui/src/api/schema.d.ts`.

- [ ] **Step 5: CHANGELOG** under `## [Unreleased]`:

```markdown
### Added

- Group sync tuning: align the Sendspin players of a Music Assistant sync
  group from the bridge — manual sliders with a click track and solo
  listening, a phone-microphone measurement with a reviewable proposal or
  one-shot apply with verification and undo, and named delay profiles
  (`SYNC_PROFILES`). New `/api/v1/sync/*` endpoints and `sync` events.
```

Run `uv run python scripts/lint_changelog.py CHANGELOG.md` — Expected: no `Rx (line N)` output.

- [ ] **Step 6: Commit** `git add -A && git commit -m "feat(sync): /api/v1/sync endpoints, models and contract export"`.

---

## Stage 3 — UI part 1: group screen, profiles, manual tuning

Conventions: components use `useI18n()`, kit components from `@/kit` (`SbCard`, `SbButton`, `SbBadge`, `SbSlider`, `SbInput`, `SbDialog`, `SbEmptyState`), stores are Pinia setup stores, API calls go through `api()`/`unwrap` from `@/api/client` against the regenerated `schema.d.ts`. Component tests mount with `createI18n({legacy: false, locale: 'en', messages: {en}})`, `createPinia()` and a memory router (see `ui/tests/component/layout/AppSidebar.test.ts`); API modules are `vi.mock`ed.

### Task 3.1: API module, store, `sync` event type, route and entry points

**Files:**
- Create: `ui/src/api/sync.ts`, `ui/src/stores/sync.ts`, `ui/src/views/SyncView.vue` (shell: header + `<router-view>`-free tabs: "Group", "Manual", "Measure" — the last two filled in 3.3/4.2), `ui/tests/unit/stores/sync.test.ts`
- Modify: `ui/src/api/types.ts` (`EventEnvelope['type']` adds `'sync'`; `SyncGroup`, `SyncSession`, `SyncProfile`, `SyncMeasurement`, `SyncProposal` type aliases from `Schemas`), `ui/src/router/index.ts` (`/sync/:groupId`, name `sync`, `meta.title: 'nav.sync'`), `ui/src/stores/bridge.ts` (`types: ['status', 'job', 'sync']` and forward `sync` events to `useSyncStore().applyEvent(event.data)`), `ui/src/components/ma/MaGroupList.vue` (a **Sync** `SbButton` per group → `router.push({name: 'sync', params: {groupId: group.id}})`), `ui/src/components/devices/DeviceTimingPanel.vue` (link "Tune the whole group" when `bridge.deviceGroup(device)` is known), `ui/src/i18n/en.json`, `ui/src/i18n/ru.json`

**Interfaces:**
- Produces (`ui/src/api/sync.ts`):

```ts
export function getSyncGroups(): Promise<SyncGroup[]>
export function getSyncGroup(groupId: string): Promise<SyncGroup>
export function writeGroupDelays(groupId: string, delays: Record<string, number>): Promise<SyncGroup>
export function createSession(body: { group_id: string; mode: 'manual' | 'semi' | 'auto'; point?: 'device' | 'position' }): Promise<SyncSession>
export function getSession(id: string): Promise<SyncSession>
export function endSession(id: string): Promise<void>
export function sessionSignal(id: string, action: 'start' | 'stop'): Promise<SyncSession>
export function sessionSolo(id: string, member: string | null): Promise<SyncSession>
export function sessionAb(id: string, a: string, b: string): Promise<SyncSession>
export function sessionReference(id: string, member: string): Promise<SyncSession>
export function sessionDelay(id: string, member: string, ms: number): Promise<SyncSession>
export function sessionSkip(id: string, member: string): Promise<SyncSession>
export function getProposal(id: string): Promise<SyncProposal>
export function applyProposal(id: string, rows?: Record<string, number>): Promise<SyncSession>
export function undoApply(id: string): Promise<SyncSession>
export function verifySession(id: string): Promise<SyncSession>
export async function uploadRecording(id: string, member: string, startTimeS: number, sampleRate: number, pcm: Int16Array): Promise<SyncMeasurement>
  // raw fetch: apiUrl(`/api/v1/sync/sessions/${id}/recordings?member=…&start_time_s=…&sample_rate=…`), method POST,
  // headers {'content-type': 'application/octet-stream', 'X-CSRF-Token': getCsrfToken()}, credentials 'same-origin', body pcm.buffer;
  // non-OK → throw new ApiError(await res.json())
export function listProfiles(groupId: string): Promise<SyncProfile[]>; createProfile(body): Promise<SyncProfile>; patchProfile(id, body); deleteProfile(id); applyProfile(id): Promise<{applied: Record<string, number>; skipped: string[]}>; captureProfile(groupId, body: {name: string; kind?: 'device' | 'position'}): Promise<SyncProfile>
```

Store (`ui/src/stores/sync.ts`): `groups`, `group(groupId)`, `session: SyncSession | null`, `profiles`, `loading`, `error`; actions `fetchGroup(groupId)`, `fetchProfiles(groupId)`, `start(groupId, mode, point)`, `end()`, `applyEvent(session)` (replaces `session` when ids match or when none is held and `session.group_id` is the current group), `setDelay(member, ms)` (debounced 300 ms per member, through `sessionDelay` when a manual session is open, else `writeGroupDelays`), `signal(action)`, `solo(member)`, `ab(a, b)`, `saveProfile(name, kind)`, `applyProfile(id)`, `renameProfile(id, name)`, `removeProfile(id)`.

- [ ] **Step 1: Failing tests** (`ui/tests/unit/stores/sync.test.ts`):

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/sync', () => ({
  getSyncGroup: vi.fn(), writeGroupDelays: vi.fn(), createSession: vi.fn(), endSession: vi.fn(), sessionDelay: vi.fn(),
  listProfiles: vi.fn(), captureProfile: vi.fn(), applyProfile: vi.fn(), patchProfile: vi.fn(), deleteProfile: vi.fn(),
  sessionSignal: vi.fn(), sessionSolo: vi.fn(), sessionAb: vi.fn(),
}))
import * as api from '@/api/sync'
import { useSyncStore } from '@/stores/sync'

const group = { id: 'g', name: 'Downstairs', session_id: null, members: [
  { ma_player_id: 'a', name: 'A', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null } ] }

describe('sync store', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks() })

  it('debounces live delay writes per member and goes through the session when one is open', async () => {
    const store = useSyncStore()
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    await store.fetchGroup('g')
    vi.mocked(api.writeGroupDelays).mockResolvedValue(group as never)
    store.setDelay('a', 10); store.setDelay('a', 20); store.setDelay('a', 30)
    vi.advanceTimersByTime(299); expect(api.writeGroupDelays).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1); expect(api.writeGroupDelays).toHaveBeenCalledTimes(1)
    expect(api.writeGroupDelays).toHaveBeenCalledWith('g', { a: 30 })
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', mode: 'manual', state: 'measuring', members: group.members } as never)
    await store.start('g', 'manual', 'device')
    vi.mocked(api.sessionDelay).mockResolvedValue({ id: 's1', state: 'measuring' } as never)
    store.setDelay('a', 40); vi.advanceTimersByTime(300)
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'a', 40)
  })

  it('applies sync events only for its own session', async () => {
    const store = useSyncStore()
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', state: 'measuring' } as never)
    await store.start('g', 'semi', 'device')
    store.applyEvent({ id: 'other', group_id: 'g2', state: 'proposed' } as never)
    expect(store.session?.id).toBe('s1')
    store.applyEvent({ id: 's1', group_id: 'g', state: 'proposed' } as never)
    expect(store.session?.state).toBe('proposed')
  })
})
```

- [ ] **Step 2: Run** `cd ui && npx vitest run tests/unit/stores/sync.test.ts` — Expected: FAIL (module not found).

- [ ] **Step 3: Implement** `api/sync.ts` (one function per route as listed; `uploadRecording` as the raw fetch described), `stores/sync.ts`:

```ts
export const useSyncStore = defineStore('sync', () => {
  const group = ref<SyncGroup | null>(null); const session = ref<SyncSession | null>(null)
  const profiles = ref<SyncProfile[]>([]); const loading = ref(false); const error = ref<string | null>(null)
  const pending = new Map<string, ReturnType<typeof setTimeout>>()
  const latest = new Map<string, number>()

  async function fetchGroup(groupId: string) { loading.value = true; try { group.value = await api.getSyncGroup(groupId) } finally { loading.value = false } }
  async function fetchProfiles(groupId: string) { profiles.value = await api.listProfiles(groupId) }
  async function start(groupId: string, mode: SessionMode, point: SessionPoint = 'device') { session.value = await api.createSession({ group_id: groupId, mode, point }) }
  async function end() { if (session.value) { await api.endSession(session.value.id); } }
  function applyEvent(next: SyncSession) {
    if (session.value ? next.id === session.value.id : group.value?.id === next.group_id) session.value = next
  }
  function setDelay(member: string, ms: number) {
    latest.set(member, ms); const t = pending.get(member); if (t) clearTimeout(t)
    pending.set(member, setTimeout(() => {
      pending.delete(member); const value = latest.get(member)!
      const m = group.value?.members.find((x) => x.ma_player_id === member); if (m) m.delay_ms = value
      const call = session.value && session.value.mode === 'manual' && session.value.state !== 'done'
        ? api.sessionDelay(session.value.id, member, value) : api.writeGroupDelays(group.value!.id, { [member]: value })
      void call.catch((e) => { error.value = e instanceof ApiError ? e.code : 'error' })
    }, 300))
  }
  …signal/solo/ab/profile actions call the API and assign the returned session/profiles…
  return { group, session, profiles, loading, error, fetchGroup, fetchProfiles, start, end, applyEvent, setDelay, signal, solo, ab, saveProfile, applyProfile, renameProfile, removeProfile }
})
```

Router entry, `bridge.ts` forwarding (`else if (event.type === 'sync') useSyncStore().applyEvent(event.data as SyncSession)`), `types.ts`, `MaGroupList.vue` button (label `t('sync.open')`), `DeviceTimingPanel.vue` link, i18n keys `nav.sync: "Group sync"`, `sync.open: "Sync"`, `sync.tuneGroup: "Tune the whole group"` (+ ru: `"Синхронизация группы"`, `"Синхронизация"`, `"Настроить всю группу"`).

- [ ] **Step 4: Run** `cd ui && npm test && npm run build` — Expected: PASS, build clean (vue-tsc).

- [ ] **Step 5: Commit** `git add ui && git commit -m "feat(ui): sync API client, store, route and entry points"`.

---

### Task 3.2: Group screen and profiles

**Files:**
- Create: `ui/src/components/sync/GroupMembers.vue`, `ui/src/components/sync/ProfilesPanel.vue`, `ui/src/components/sync/ProfileDiffDialog.vue`, `ui/tests/component/sync/GroupMembers.test.ts`, `ui/tests/component/sync/ProfilesPanel.test.ts`
- Modify: `ui/src/views/SyncView.vue` (Group tab = `GroupMembers` + `ProfilesPanel` + three action buttons: Manual tuning → tab `manual`; Measure and suggest → wizard `semi`; Measure and apply → wizard `auto`), i18n en/ru

**Interfaces:**
- Consumes: `useSyncStore()` (`group`, `profiles`, `saveProfile`, `applyProfile`, `renameProfile`, `removeProfile`), `confirmDialog` from `@/composables/useConfirm`, `useNotificationStore()`.
- Produces: `GroupMembers` props `{ members: SyncMember[] }` renders name, origin badge (`sync.origin.bridge|sendspin|other` → "Bridge" / "Sendspin" / "Cannot be adjusted"), `delay_ms` ("— " when null), `last_measured_at` relative age via `useNow`. `ProfilesPanel` props `{ groupId: string }`; emits nothing; `ProfileDiffDialog` props `{ profile, members }` shows rows member → current → profile and the list of members not in the group, emits `apply`.

- [ ] **Step 1: Failing tests**

```ts
// ui/tests/component/sync/GroupMembers.test.ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import GroupMembers from '@/components/sync/GroupMembers.vue'
import en from '@/i18n/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

describe('GroupMembers', () => {
  it('shows origin, adjustability and the current delay', () => {
    const w = mount(GroupMembers, { global: { plugins: [i18n] }, props: { members: [
      { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 120, last_measured_at: null },
      { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null, last_measured_at: null },
    ] } })
    const rows = w.findAll('[data-member]')
    expect(rows[0]!.text()).toContain('Kitchen'); expect(rows[0]!.text()).toContain('Bridge'); expect(rows[0]!.text()).toContain('120 ms')
    expect(rows[1]!.text()).toContain('Cannot be adjusted'); expect(rows[1]!.text()).not.toContain('ms')
  })
})
```

```ts
// ui/tests/component/sync/ProfilesPanel.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import ProfilesPanel from '@/components/sync/ProfilesPanel.vue'
import { useSyncStore } from '@/stores/sync'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({ listProfiles: vi.fn(), applyProfile: vi.fn(), captureProfile: vi.fn(), patchProfile: vi.fn(), deleteProfile: vi.fn(), getSyncGroup: vi.fn() }))
import * as api from '@/api/sync'
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'A', origin: 'bridge', adjustable: true, delay_ms: 0 }, { ma_player_id: 'b', name: 'B', origin: 'sendspin', adjustable: true, delay_ms: 50 }]

describe('ProfilesPanel', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('lists profiles, shows the diff before applying and reports skipped members', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: { a: 120, b: 0, gone: 30 }, created_at: '2026-10-15T10:00:00Z', notes: '' }] as never)
    vi.mocked(api.applyProfile).mockResolvedValue({ applied: { a: 120, b: 0 }, skipped: ['gone'] })
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body })
    await flushPromises()
    expect(w.text()).toContain('Evening')
    await w.find('[data-apply="p1"]').trigger('click'); await flushPromises()
    const dialog = document.body.querySelector('[data-profile-diff]')!
    expect(dialog.textContent).toContain('A'); expect(dialog.textContent).toContain('0 → 120'); expect(dialog.textContent).toContain('gone')
    await (dialog.querySelector('[data-confirm]') as HTMLElement).click(); await flushPromises()
    expect(api.applyProfile).toHaveBeenCalledWith('p1')
    expect(document.body.textContent).toContain('1 member skipped')
  })

  it('saves the current delays as a profile', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([]); vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p2', name: 'Now', group_id: 'g', kind: 'device', delays: { a: 0, b: 50 }, created_at: '', notes: '' } as never)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.find('[data-save-current]').trigger('click')
    await (document.body.querySelector('[data-profile-name]') as HTMLInputElement).focus()
    ;(document.body.querySelector('[data-profile-name]') as HTMLInputElement).value = 'Now'
    document.body.querySelector('[data-profile-name]')!.dispatchEvent(new Event('input'))
    await (document.body.querySelector('[data-profile-save]') as HTMLElement).click(); await flushPromises()
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Now', kind: 'device' })
    expect(w.text()).toContain('Now')
  })
})
```

- [ ] **Step 2: Run** `cd ui && npx vitest run tests/component/sync` — Expected: FAIL.

- [ ] **Step 3: Implement** the three components (template essentials):

```vue
<!-- GroupMembers.vue -->
<ul class="divide-y divide-border">
  <li v-for="m in members" :key="m.ma_player_id" data-member class="flex items-center gap-3 py-2">
    <span class="min-w-0 flex-1 truncate">{{ m.name }}</span>
    <SbBadge :tone="m.adjustable ? 'neutral' : 'warning'" size="sm">{{ t(`sync.origin.${m.adjustable ? m.origin : 'other'}`) }}</SbBadge>
    <span class="tabular-nums text-sm">{{ m.adjustable && m.delay_ms != null ? `${m.delay_ms} ms` : '—' }}</span>
    <span v-if="m.last_measured_at" class="text-xs text-text-secondary">{{ age(m.last_measured_at) }}</span>
  </li>
</ul>
```

`ProfilesPanel.vue`: list with **Apply** (`data-apply`), rename (inline `SbInput`), delete (`confirmDialog`), **Save current as…** (`data-save-current` → `SbDialog` with `data-profile-name` input and `data-profile-save`), the diff dialog (`SbDialog` with `data-profile-diff`, rows `name  before → after`, "Not in the group: …", `data-confirm`), a toast `t('sync.profiles.skipped', {n})` = "{n} member skipped" | "{n} members skipped" via `tc`. `SyncView.vue` Group tab composes them and the three action buttons.

i18n (en, mirrored in ru): `sync.origin.bridge: "Bridge"`, `sync.origin.sendspin: "Sendspin"`, `sync.origin.other: "Cannot be adjusted"`, `sync.profiles.title/apply/saveCurrent/rename/delete/empty/skipped/notInGroup/name/save/cancel`, `sync.actions.manual: "Manual tuning"`, `sync.actions.semi: "Measure and suggest"`, `sync.actions.auto: "Measure and apply"`.

- [ ] **Step 4: Run** `cd ui && npm test && npm run build` — Expected: PASS.

- [ ] **Step 5: Commit** `git add ui && git commit -m "feat(ui): group sync screen with members and profiles"`.

---

### Task 3.3: Manual tuning

**Files:**
- Create: `ui/src/components/sync/ManualTuning.vue`, `ui/src/components/sync/ListeningBar.vue`, `ui/src/components/sync/DelayRow.vue`, `ui/tests/component/sync/ManualTuning.test.ts`
- Modify: `ui/src/views/SyncView.vue` (Manual tab: on enter `store.start(groupId, 'manual')` if no session, on leave `store.end()` after offering "Save as profile?"), i18n en/ru

**Interfaces:**
- Consumes: store `session`, `setDelay`, `signal`, `solo`, `ab`, `end`, `saveProfile`; `SbSlider` (`min`, `max`, `step`, `ariaLabel`, `v-model`).
- Produces: `DelayRow` props `{ member: SyncMember, modelValue: number }` emits `update:modelValue` (slider 0–5000 step 1, buttons −10/−1/+1/+10 with `aria-label`, number input; all clamp 0..5000); `ListeningBar` props `{ members, signalRunning, soloed }` emits `signal('start'|'stop')`, `solo(member|null)`, `ab(a,b)`; `ManualTuning` orchestrates: `Reset to session start` (writes each member's snapshot delay — kept in the component as `initial` from the first `session.members`).

A/B: the server only solos one member; the component alternates `solo(a)`/`solo(b)` every **4 s** (two clicks at P = 2 s) with `setInterval`, and stops on any other action.

- [ ] **Step 1: Failing tests**

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import ManualTuning from '@/components/sync/ManualTuning.vue'
import { useSyncStore } from '@/stores/sync'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({ sessionDelay: vi.fn().mockResolvedValue({}), sessionSignal: vi.fn().mockResolvedValue({}), sessionSolo: vi.fn().mockResolvedValue({}), sessionAb: vi.fn().mockResolvedValue({}), writeGroupDelays: vi.fn(), getSyncGroup: vi.fn(), listProfiles: vi.fn(), captureProfile: vi.fn(), createSession: vi.fn(), endSession: vi.fn() }))
import * as api from '@/api/sync'
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'A', origin: 'bridge', adjustable: true, delay_ms: 100 }, { ma_player_id: 'b', name: 'B', origin: 'sendspin', adjustable: true, delay_ms: 0 }, { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null }]

function setup() {
  setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks()
  const store = useSyncStore()
  store.group = { id: 'g', name: 'G', session_id: 's1', members } as never
  store.session = { id: 's1', group_id: 'g', mode: 'manual', state: 'measuring', members, signal_running: false, soloed: null } as never
  return mount(ManualTuning, { global: { plugins: [i18n] } })
}

describe('ManualTuning', () => {
  it('nudges write the clamped value live, debounced, and non-adjustable members have no controls', async () => {
    const w = setup()
    const rows = w.findAll('[data-delay-row]'); expect(rows).toHaveLength(2)
    await rows[1]!.find('[aria-label="−10 ms"]').trigger('click')          // 0 → clamped 0
    await rows[0]!.find('[aria-label="+10 ms"]').trigger('click'); await rows[0]!.find('[aria-label="+1 ms"]').trigger('click')
    vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'a', 111)
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'b', 0)
    expect(w.text()).toContain('TV')
  })

  it('plays clicks, solos and alternates A/B every four seconds', async () => {
    const w = setup()
    await w.find('[data-signal]').trigger('click'); expect(api.sessionSignal).toHaveBeenCalledWith('s1', 'start')
    await w.find('[data-solo="a"]').trigger('click'); expect(api.sessionSolo).toHaveBeenCalledWith('s1', 'a')
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    expect(api.sessionAb).toHaveBeenCalledWith('s1', 'a', 'b')
    vi.advanceTimersByTime(4000); await flushPromises(); expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', 'b')
    vi.advanceTimersByTime(4000); await flushPromises(); expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', 'a')
    await w.find('[data-solo-none]').trigger('click'); vi.advanceTimersByTime(8000); await flushPromises()
    expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', null)
  })

  it('reset returns every member to the session-start delay', async () => {
    const w = setup()
    await w.findAll('[data-delay-row]')[0]!.find('[aria-label="+10 ms"]').trigger('click'); vi.advanceTimersByTime(300)
    await w.find('[data-reset]').trigger('click'); vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 100)
  })
})
```

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement.** `DelayRow.vue`:

```vue
<script setup lang="ts">
const props = defineProps<{ member: SyncMember; modelValue: number }>()
const emit = defineEmits<{ 'update:modelValue': [number] }>()
const clamp = (v: number) => Math.max(0, Math.min(5000, Math.round(v)))
const nudge = (d: number) => emit('update:modelValue', clamp(props.modelValue + d))
</script>
<template>
  <div data-delay-row class="space-y-1 py-2">
    <div class="flex items-center justify-between"><span class="font-medium">{{ member.name }}</span>
      <input type="number" min="0" max="5000" :value="modelValue" :aria-label="member.name" class="w-20 …" @change="emit('update:modelValue', clamp(Number(($event.target as HTMLInputElement).value)))" /></div>
    <SbSlider :model-value="modelValue" :min="0" :max="5000" :step="1" :aria-label="member.name" :format-value="(v) => `${v} ms`" @update:model-value="emit('update:modelValue', clamp($event))" />
    <div class="flex gap-1">
      <SbButton v-for="d in [-10, -1, 1, 10]" :key="d" size="sm" variant="secondary" :aria-label="`${d > 0 ? '+' : '−'}${Math.abs(d)} ms`" @click="nudge(d)">{{ d > 0 ? '+' : '−' }}{{ Math.abs(d) }}</SbButton>
    </div>
  </div>
</template>
```

`ManualTuning.vue` holds `values = reactive<Record<string, number>>` seeded from `session.members` (adjustable only) and `initial` (frozen copy); `watch(values)` → `store.setDelay(member, value)`; `data-reset` writes `initial` back through `setDelay`; renders non-adjustable members as plain rows with the "Cannot be adjusted" badge. `ListeningBar.vue`: `data-signal` toggle (`sync.listen.play` / `sync.listen.stop`), `data-solo="<id>"` per adjustable member, `data-solo-none` ("Everyone"), `data-ab` ("A/B") with two selects defaulting to the first two members; the A/B interval lives in `ManualTuning` (`setInterval(…, 4000)`, cleared on solo/none/unmount). On unmount `SyncView` calls `confirmDialog(t('sync.manual.saveOnLeave'))` → `saveProfile(name)` dialog → `store.end()`.

- [ ] **Step 4: Run** `cd ui && npm test && npm run build` — Expected: PASS.

- [ ] **Step 5: Commit** `git add ui && git commit -m "feat(ui): manual group tuning with click track, solo and A/B"`.

---

## Stage 4 — UI part 2: measurement wizard

### Task 4.1: AudioWorklet capture

**Files:**
- Create: `ui/src/sync/sync-capture.worklet.ts`, `ui/src/sync/decimate.ts`, `ui/src/sync/capture.ts`, `ui/tests/unit/sync/decimate.test.ts`, `ui/tests/unit/sync/capture.test.ts`
- Modify: `ui/vite.config.ts` only if `new URL('./sync-capture.worklet.ts', import.meta.url)` is not emitted as a hashed asset (Vite 5 does this by default — verify `ls ui/dist/assets | grep worklet` after `npm run build`)

**Interfaces:**
- Consumes: `useIngress()` (`basePath`) for the module URL; the stage 0 note's mute latency → `SOLO_SETTLE_MS`.
- Produces:

```ts
// decimate.ts
export function decimationFactor(contextRate: number): number        // floor(rate / 16000), ≥ 1 → 48000→3, 44100→2, 16000→1
export function lowpassDecimate(input: Float32Array, factor: number, state: { acc: number[]; phase: number }): Float32Array
  // 4-tap moving average per factor window (cheap anti-alias for a 2–6 kHz target), keeps `state` across blocks
export function toInt16(samples: Float32Array): Int16Array             // clamp, round

// capture.ts
export const SOLO_SETTLE_MS = 800
export interface Capture { pcm: Int16Array; sampleRate: number; startTimeS: number }
export class SyncRecorder {
  static supported(): boolean                                           // isSecureContext && getUserMedia && AudioWorkletNode
  async open(): Promise<void>                                           // getUserMedia (EC/NS/AGC false, mono) THEN new AudioContext(); addModule(workletUrl()); keep-alive gain
  async record(durationS: number): Promise<Capture>                     // posts {cmd:'start'} to the worklet, resolves with the worklet's startFrame/sampleRate
  level(): number                                                        // last RMS reported by the worklet (0..1) for the level meter
  get epochInvalidated(): boolean                                        // set when context.state left 'running'
  close(): void
}
export function workletUrl(): string                                    // new URL('./sync-capture.worklet.ts', import.meta.url).href — Vite rewrites it to the hashed asset under the app base
```

Worklet protocol: main → `{cmd:'start', frames}`; worklet → `{type:'level', rms}` every 128 ms while idle, `{type:'started', startFrame, sampleRate}` on the first processed block after start, then `{type:'chunk', data: Float32Array}` blocks, `{type:'done'}` after `frames`. The worklet forwards raw blocks; decimation runs on the main thread (keeps the worklet trivial and testable).

- [ ] **Step 1: Failing tests**

```ts
// decimate.test.ts
import { describe, it, expect } from 'vitest'
import { decimationFactor, lowpassDecimate, toInt16 } from '@/sync/decimate'

describe('decimate', () => {
  it('picks the integer factor that lands at or above 16 kHz', () => {
    expect(decimationFactor(48000)).toBe(3); expect(decimationFactor(44100)).toBe(2); expect(decimationFactor(16000)).toBe(1); expect(decimationFactor(8000)).toBe(1)
  })
  it('keeps the sample count consistent across blocks', () => {
    const state = { acc: [], phase: 0 }
    const a = lowpassDecimate(new Float32Array(100).fill(0.5), 3, state); const b = lowpassDecimate(new Float32Array(50).fill(0.5), 3, state)
    expect(a.length + b.length).toBe(50); expect(Math.abs(b[b.length - 1]! - 0.5)).toBeLessThan(1e-6)
  })
  it('converts to clamped int16', () => {
    expect(Array.from(toInt16(new Float32Array([0, 0.5, 1.5, -2])))).toEqual([0, 16384, 32767, -32768])
  })
})
```

```ts
// capture.test.ts — a fake AudioContext/AudioWorkletNode
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SyncRecorder } from '@/sync/capture'

class FakePort { onmessage: ((e: MessageEvent) => void) | null = null; posted: unknown[] = []; postMessage(m: unknown) { this.posted.push(m) } }
class FakeNode { port = new FakePort(); connect() {} disconnect() {} }
class FakeContext {
  state = 'running'; sampleRate = 48000; currentTime = 0; audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) }
  listeners: Record<string, () => void> = {}
  createMediaStreamSource() { return { connect() {} } } createGain() { return { gain: { value: 1 }, connect() {} } } get destination() { return {} }
  addEventListener(n: string, f: () => void) { this.listeners[n] = f } resume() { return Promise.resolve() } close() { return Promise.resolve() }
}
let node: FakeNode
beforeEach(() => {
  node = new FakeNode()
  vi.stubGlobal('AudioContext', FakeContext); vi.stubGlobal('AudioWorkletNode', vi.fn(() => node))
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop() {} }] }) } })
  vi.stubGlobal('isSecureContext', true)
})

describe('SyncRecorder', () => {
  it('opens the microphone before the context and stamps recordings with the worklet clock', async () => {
    const order: string[] = []
    ;(navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockImplementation(async () => { order.push('mic'); return { getTracks: () => [] } })
    const OrigCtx = globalThis.AudioContext; vi.stubGlobal('AudioContext', class extends OrigCtx { constructor() { super(); order.push('ctx') } })
    const r = new SyncRecorder(); await r.open()
    expect(order).toEqual(['mic', 'ctx'])
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } })
    const p = r.record(0.01)
    expect(node.port.posted[0]).toEqual({ cmd: 'start', frames: 480 })
    node.port.onmessage!({ data: { type: 'started', startFrame: 96000, sampleRate: 48000 } } as MessageEvent)
    node.port.onmessage!({ data: { type: 'chunk', data: new Float32Array(480).fill(0.25) } } as MessageEvent)
    node.port.onmessage!({ data: { type: 'done' } } as MessageEvent)
    const cap = await p
    expect(cap.startTimeS).toBe(2); expect(cap.sampleRate).toBe(16000); expect(cap.pcm.length).toBe(160)
  })
  it('flags the epoch when the context stops running', async () => {
    const r = new SyncRecorder(); await r.open()
    const ctx = (r as unknown as { ctx: FakeContext }).ctx; ctx.state = 'suspended'; ctx.listeners['statechange']!()
    expect(r.epochInvalidated).toBe(true)
  })
})
```

- [ ] **Step 2: Run** `cd ui && npx vitest run tests/unit/sync` — Expected: FAIL.

- [ ] **Step 3: Implement.** Worklet:

```ts
// sync-capture.worklet.ts
class SyncCaptureProcessor extends AudioWorkletProcessor {
  private remaining = 0; private started = false; private levelAcc = 0; private levelN = 0
  constructor() { super(); this.port.onmessage = (e) => { if (e.data?.cmd === 'start') { this.remaining = e.data.frames; this.started = false } } }
  process(inputs: Float32Array[][]) {
    const ch = inputs[0]?.[0]; if (!ch) return true
    if (this.remaining > 0) {
      if (!this.started) { this.started = true; this.port.postMessage({ type: 'started', startFrame: currentFrame, sampleRate }) }
      const n = Math.min(ch.length, this.remaining); this.port.postMessage({ type: 'chunk', data: ch.slice(0, n) }); this.remaining -= n
      if (this.remaining === 0) this.port.postMessage({ type: 'done' })
    } else {
      for (const v of ch) this.levelAcc += v * v; this.levelN += ch.length
      if (this.levelN >= sampleRate / 8) { this.port.postMessage({ type: 'level', rms: Math.sqrt(this.levelAcc / this.levelN) }); this.levelAcc = 0; this.levelN = 0 }
    }
    return true
  }
}
registerProcessor('sync-capture', SyncCaptureProcessor)
```

`capture.ts` as specified: `open()` → `getUserMedia` first, then `this.ctx = new AudioContext()`, `await ctx.audioWorklet.addModule(workletUrl())`, `node = new AudioWorkletNode(ctx, 'sync-capture')`, source → node → silent gain (value 0) → destination (keeps the graph alive), `ctx.addEventListener('statechange', …)` sets `epochInvalidated` when `state !== 'running'`; `record(durationS)` posts `{cmd:'start', frames: Math.round(durationS * ctx.sampleRate)}`, collects chunks through `lowpassDecimate` with `decimationFactor(ctx.sampleRate)`, resolves `{pcm: toInt16(concat), sampleRate: ctx.sampleRate / factor, startTimeS: startFrame / sampleRate}`. `workletUrl()` returns `new URL('./sync-capture.worklet.ts', import.meta.url).href`; add `/// <reference types="@types/audioworklet" />` or a minimal `ui/src/sync/audioworklet.d.ts` declaring `AudioWorkletProcessor`, `registerProcessor`, `currentFrame`, `sampleRate` for vue-tsc.

- [ ] **Step 4: Run** `cd ui && npm test && npm run build && ls dist/assets | grep -i worklet` — Expected: PASS and one `sync-capture.worklet-*.js` asset. Open the built app under a fake ingress prefix (`VITE` preview with `?ingress=/api/hassio_ingress/x` is not available — instead `grep -o 'sync-capture.worklet[^"]*' dist/assets/*.js` and confirm the reference is relative to `import.meta.url`, not absolute `/assets/…`).

- [ ] **Step 5: Commit** `git add ui && git commit -m "feat(ui): AudioWorklet capture with a shared session clock"`.

---

### Task 4.2: Wizard steps 1–3 (preparation, level check, member by member)

**Files:**
- Create: `ui/src/components/sync/MeasureWizard.vue`, `ui/src/components/sync/StepPrepare.vue`, `ui/src/components/sync/StepLevel.vue`, `ui/src/components/sync/StepMembers.vue`, `ui/src/components/sync/LevelMeter.vue`, `ui/tests/component/sync/MeasureWizard.test.ts`
- Modify: `ui/src/views/SyncView.vue` (Measure tab mounts `MeasureWizard` with `mode` from the chosen action), i18n en/ru

**Interfaces:**
- Consumes: `SyncRecorder` (injected as a prop `recorderFactory: () => SyncRecorder` for tests), store `start(groupId, mode, point)`, `signal`, `solo`, `session`, `uploadRecording` from `@/api/sync`, `sessionSkip`, `sessionReference`.
- Produces: `MeasureWizard` props `{ groupId: string; mode: 'semi' | 'auto'; recorderFactory?: () => SyncRecorder }`, emits `done` (→ Task 4.3 takes over from `proposed`). Internal step state `prepare → level → members → (proposal: 4.3)`; `aria-live="polite"` region `[data-step-status]` announcing step changes and results. Member order editable (move up/down buttons); per member: prompt (`sync.wizard.holdNear` with the name, or `sync.wizard.stayPut` for `position`), **Record** → `solo(member)` → wait `SOLO_SETTLE_MS` → `recorder.record(point === 'device' ? 6 : 12)` → `uploadRecording` → result badge (✓ confidence %, "Not heard", "Unstable") with **Repeat** / **Skip**. If `recorder.epochInvalidated` → banner `sync.wizard.restartRound` and all results cleared (`session` measurements are re-recorded).

- [ ] **Step 1: Failing test**

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import { useSyncStore } from '@/stores/sync'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({ createSession: vi.fn(), sessionSignal: vi.fn().mockResolvedValue({}), sessionSolo: vi.fn().mockResolvedValue({}), uploadRecording: vi.fn(), sessionSkip: vi.fn().mockResolvedValue({}), sessionReference: vi.fn(), getProposal: vi.fn(), endSession: vi.fn(), getSyncGroup: vi.fn(), listProfiles: vi.fn() }))
import * as api from '@/api/sync'
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 }, { ma_player_id: 'b', name: 'Hall', origin: 'sendspin', adjustable: true, delay_ms: 0 }, { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null }]

class FakeRecorder {
  static supported = () => true; epochInvalidated = false; opened = false; recorded: number[] = []
  async open() { this.opened = true } level() { return 0.2 }
  async record(d: number) { this.recorded.push(d); return { pcm: new Int16Array(16000 * d), sampleRate: 16000, startTimeS: 20 } }
  close() {}
}

describe('MeasureWizard', () => {
  let rec: FakeRecorder
  beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks(); rec = new FakeRecorder() })

  it('walks prepare → level → members, soloing, recording and uploading each adjustable member', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, measurements: {}, signal_running: true } as never)
    vi.mocked(api.uploadRecording).mockResolvedValueOnce({ member: 'a', status: 'ok', confidence: 0.8, clicks: 3, phase_ms: 1, spread_ms: 0.4 } as never)
                                  .mockResolvedValueOnce({ member: 'b', status: 'not_heard', reason: 'no_clicks' } as never)
    const w = mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', mode: 'semi', recorderFactory: () => rec as never }, attachTo: document.body })
    expect(w.text()).toContain('Playback will be interrupted')
    await w.find('[data-next]').trigger('click'); await flushPromises()
    expect(rec.opened).toBe(true); expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'semi', point: 'device' })
    expect(w.find('[data-level-meter]').exists()).toBe(true)
    await w.find('[data-next]').trigger('click'); await flushPromises()
    expect(w.findAll('[data-member-step]')).toHaveLength(2)                      // TV is not measured
    expect(w.find('[data-step-status]').attributes('aria-live')).toBe('polite')
    await w.find('[data-record="a"]').trigger('click'); await flushPromises()
    expect(api.sessionSolo).toHaveBeenCalledWith('s1', 'a')
    vi.advanceTimersByTime(800); await flushPromises(); await flushPromises()
    expect(rec.recorded).toEqual([6]); expect(api.uploadRecording).toHaveBeenCalledWith('s1', 'a', 20, 16000, expect.any(Int16Array))
    expect(w.find('[data-result="a"]').text()).toContain('80 %')
    await w.find('[data-record="b"]').trigger('click'); vi.advanceTimersByTime(800); await flushPromises(); await flushPromises()
    expect(w.find('[data-result="b"]').text()).toContain('Not heard')
    expect(w.find('[data-repeat="b"]').exists()).toBe(true)
    await w.find('[data-skip="b"]').trigger('click'); expect(api.sessionSkip).toHaveBeenCalledWith('s1', 'b')
  })

  it('restarts the round when the audio context stopped', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, measurements: {}, signal_running: true } as never)
    const w = mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', mode: 'semi', recorderFactory: () => rec as never } })
    await w.find('[data-next]').trigger('click'); await flushPromises(); await w.find('[data-next]').trigger('click'); await flushPromises()
    rec.epochInvalidated = true
    await w.find('[data-record="a"]').trigger('click'); await flushPromises()
    expect(w.text()).toContain('start the measurement again'); expect(api.uploadRecording).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run** `cd ui && npx vitest run tests/component/sync/MeasureWizard.test.ts` — Expected: FAIL.

- [ ] **Step 3: Implement** the four components. `MeasureWizard.vue` state: `step: 'prepare' | 'level' | 'members' | 'proposal' | 'done'`, `point`, `order: string[]` (adjustable members), `results: Record<string, SyncMeasurement>`, `busy`. Flow on `data-next` from `prepare`: `recorder = props.recorderFactory?.() ?? new SyncRecorder()`; `await recorder.open()` (catch → `sync.wizard.micDenied`); `await store.start(groupId, mode, point)`; step `level` (signal is already running after `start`; `LevelMeter` polls `recorder.level()` every 200 ms, hints `tooQuiet` < 0.02, `tooLoud` > 0.7). `members`: `StepMembers` with `data-member-step`, `data-record`, `data-repeat`, `data-skip`, `data-result`, move up/down; `record(member)`: if `recorder.epochInvalidated` → set `restartRound = true`, clear `results`, show `sync.wizard.restartRound` ("The microphone clock was interrupted — start the measurement again") and return; else `await store.solo(member)`; `await new Promise(r => setTimeout(r, SOLO_SETTLE_MS))`; `const cap = await recorder.record(point === 'device' ? 6 : 12)`; `results[member] = await uploadRecording(session.id, member, cap.startTimeS, cap.sampleRate, cap.pcm)`; `ApiError` → `results[member] = {status: 'not_heard', reason: e.code}`. When every member in `order` has a result (ok or skipped) → emit `proposal` (4.3). i18n keys under `sync.wizard.*` (en + ru): `interrupted: "Playback will be interrupted and restored afterwards."`, `point.device/position`, `holdStill`, `micDenied`, `level.title/tooQuiet/tooLoud`, `holdNear: "Hold the phone 30–50 cm from {name}"`, `stayPut: "Stay where you are"`, `record/repeat/skip/notHeard/unstable/confidence: "{pct} %"`, `restartRound`, `next/back`.

- [ ] **Step 4: Run** `cd ui && npm test && npm run build` — Expected: PASS.

- [ ] **Step 5: Commit** `git add ui && git commit -m "feat(ui): measurement wizard steps — preparation, level check, members"`.

---

### Task 4.3: Proposal, apply, undo, verification, done, failure handling, ru

**Files:**
- Create: `ui/src/components/sync/StepProposal.vue`, `ui/src/components/sync/SpreadChart.vue`, `ui/src/components/sync/StepDone.vue`, `ui/tests/component/sync/StepProposal.test.ts`
- Modify: `ui/src/components/sync/MeasureWizard.vue`, `ui/src/views/SyncView.vue` (read-only banner when `group.session_id` belongs to another client; "Could not restore the group" banner with **Retry** while `session.state === 'restoring'` and `session.error`), `ui/src/i18n/ru.json` (every `sync.*` key)

**Interfaces:**
- Consumes: `getProposal`, `applyProposal`, `undoApply`, `verifySession`, `endSession`, `captureProfile`; `session.undo_until_s`, `session.history`, `proposal.rows/warnings/unverified/predicted_spread_ms`.
- Produces: `StepProposal` props `{ session: SyncSession; proposal: SyncProposal; mode }`, emits `apply(rows?)`, `applyAndSave(name)`, `cancel`; renders table `name | before | after | Δ` (`data-row="<id>"`), clamped rows with a warning icon, `SpreadChart` (two bars: before/after spread from `history`), warnings list, "Could not be verified: …" for `unverified`, and when `session.error === 'ma_forbidden'` the copyable value list (`data-copy-values`, `navigator.clipboard.writeText`) plus the hint `sync.proposal.forbiddenHint`. Automatic mode: `MeasureWizard` calls `applyProposal` itself as soon as `state === 'proposed'`, shows **Undo (30 s)** (`data-undo`, counting down from `undo_until_s`), and the verification round reuses `StepMembers` with the title `sync.wizard.verifying`; a second proposal from the server (round 2) is applied automatically too. `StepDone`: "Group restored" once `state ∈ {done}`, "Restoring…" while `restoring`, **Quick listen** (`sessionSignal('start')` for 10 s then `stop`).

- [ ] **Step 1: Failing test**

```ts
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import StepProposal from '@/components/sync/StepProposal.vue'
import en from '@/i18n/en.json'
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'Kitchen' }, { ma_player_id: 'b', name: 'Hall' }, { ma_player_id: 'c', name: 'Attic' }]
const proposal = { reference: 'a', rows: [{ member: 'a', before_ms: 0, after_ms: 0, delta_ms: 0, clamped: false }, { member: 'b', before_ms: 100, after_ms: 5000, delta_ms: 4900, clamped: true }],
  predicted_spread_ms: 150, unverified: ['c'], warnings: ['Some delays were clamped to 5000 ms; predicted residual 150 ms.'] }

describe('StepProposal', () => {
  it('shows before → after, clamping, unverified members and applies', async () => {
    const w = mount(StepProposal, { global: { plugins: [i18n] }, props: { session: { id: 's1', mode: 'semi', state: 'proposed', members, history: [{ round: 1, spread_before_ms: 4900 }], error: null } as never, proposal: proposal as never, mode: 'semi' } })
    expect(w.find('[data-row="b"]').text()).toContain('100'); expect(w.find('[data-row="b"]').text()).toContain('5000')
    expect(w.find('[data-row="b"]').text()).toContain('+4900'); expect(w.find('[data-row="b"] [data-clamped]').exists()).toBe(true)
    expect(w.text()).toContain('Attic'); expect(w.text()).toContain('150 ms')
    await w.find('[data-apply]').trigger('click'); expect(w.emitted('apply')).toHaveLength(1)
  })
  it('offers the values to copy when Music Assistant refused the write', async () => {
    const write = vi.fn(); vi.stubGlobal('navigator', { clipboard: { writeText: write } })
    const w = mount(StepProposal, { global: { plugins: [i18n] }, props: { session: { id: 's1', mode: 'semi', state: 'proposed', members, history: [], error: 'ma_forbidden' } as never, proposal: proposal as never, mode: 'semi' } })
    expect(w.text()).toContain('administrator')
    await w.find('[data-copy-values]').trigger('click')
    expect(write).toHaveBeenCalledWith('Kitchen: 0 ms\nHall: 5000 ms')
  })
})
```

- [ ] **Step 2: Run** — Expected: FAIL.

- [ ] **Step 3: Implement** the components and wire `MeasureWizard`: on `session.state` changes (store events) → `proposed` → `getProposal` → `StepProposal`; `apply` → `applyProposal(session.id, rows)`; `ApiError` with `code === 'ma_forbidden'` → set `session.error = 'ma_forbidden'` locally and stay; `applyAndSave` → `applyProposal` then `captureProfile(groupId, {name, kind: point})`; `auto` → apply immediately, show `data-undo` with a 1 s countdown from `undo_until_s`, `undoApply` on click; `verifying` → `StepMembers` again; `restoring`/`done` → `StepDone`; `cancel` → `endSession`. `SyncView`: when `group.session_id && !store.session` → read-only banner `sync.readOnly` ("Tuning in progress on another device") and disabled actions; restore banner with `data-retry` calling `endSession(session.id)` again (idempotent on the server: `cancel` on a restoring session is a no-op, the store's tick retries anyway — the button mainly re-fetches `getSession`). Member leaves the group mid-session (`member_gone` from any call) → the step is marked `sync.wizard.memberGone` and the order continues. Fill `ru.json` for every `sync.*` key (keep the en key set and ru key set identical — add a vitest `ui/tests/unit/i18n-sync-keys.test.ts` asserting `Object.keys(flatten(en.sync)).sort()` equals the ru list).

- [ ] **Step 4: Run** `cd ui && npm test && npm run build` — Expected: PASS. Manual check on a phone (Chrome Android, Safari iOS) against the test stand: microphone prompt, level meter moves, a 6 s recording uploads (DevTools network: `application/octet-stream`, ~192 kB), `sync` events arrive over SSE under HA ingress.

- [ ] **Step 5: Commit** `git add ui && git commit -m "feat(ui): proposal, apply, undo, verification and wizard completion"`.

---

## Stage 5 — Docs, live check, beta release

### Task 5.1: Documentation and live check

**Files:**
- Modify: `docs-site/src/content/docs/web-ui.mdx` (new section "Group sync tuning": entry points, the three modes, profiles, what the phone needs, what the operator hears during a measurement), `docs-site/src/content/docs/configuration.md` (`SYNC_PROFILES` row: "managed from the Sync screen"), `CHANGELOG.md` (if stage 2–4 added user-visible details, fold them into the existing bullet — one bullet per behaviour, R7: no file paths)
- Create: `docs/plans/2026-10-15-group-sync-tuning-live-check.md` (results of the procedure below; short)

- [ ] **Step 1:** Write the docs section (en; the ru site page mirrors it if `docs-site/src/content/docs/ru/web-ui.mdx` exists). Run `cd docs-site && npm run build` — Expected: build OK.

- [ ] **Step 2: Live check on the stand** (`ssh haos`, Music Assistant dev, bridge local outputs, web kiosk; the production bridge on 8080 of this machine is not involved): (1) set one member to +120 ms in Music Assistant's player settings; (2) open `/sync/<group>` on the phone → **Measure and suggest** → walk the wizard → the proposal must bring that member within ±5 ms of the others (its row shows ≈ −120); apply; (3) **Measure and apply** → verification round reports `spread_after_ms ≤ 5`; press **Undo** within 30 s → the +120 is back (check in MA); (4) **Manual tuning** → nudge, hear the click track, solo, A/B → values appear live in MA's slider; (5) save a profile, change a delay, apply the profile → restored; (6) kill the bridge process mid-measurement, start it again → the group is unmuted and playing within a minute, the old session answers 404; (7) run the same measurement from the listening position (`position`) and save it as a `position` profile. Record each step's outcome in the live-check file; any failure becomes a fix commit before the release.

- [ ] **Step 3: Commit** `git add docs-site docs CHANGELOG.md && git commit -m "docs: group sync tuning user guide and live check"`.

### Task 5.2: Beta release

- [ ] **Step 1:** Follow the release flow in memory (`release_pipeline_flow.md`): bump `VERSION` to the next `3.x.0-beta.N`, move the `[Unreleased]` block to a dated `## [3.x.0-beta.N] - YYYY-MM-DD` heading, run `uv run python scripts/lint_changelog.py CHANGELOG.md --fix-footer`, regenerate the HA add-on variants (`uv run python scripts/generate_ha_addon_variants.py`), `uv lock` unchanged (no new deps).
- [ ] **Step 2:** All gates once more: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`; `uv run ruff check . && uv run ruff format --check .`; `uv run python scripts/export_openapi.py --check`; `uv run python scripts/export_sync_contract.py --check`; `cd ui && npm test && npm run build && npm run check:api`.
- [ ] **Step 3: Commit** `git commit -am "release: 3.x.0-beta.N"` and hand over to the user for the push (`git push origin HEAD:beta` per the beta-branch memory); do not push from this plan.

---

## Self-review

**Spec coverage.** Goal modes: manual (3.3), semi (4.2–4.3), auto (4.3 undo/verify), profiles (2.1, 2.5, 3.2). Facts: adjustable from `get_entries`, universal key form, `return_protocol_players` (2.3); announcement semantics, `pre_announce: false`, fire-and-forget, `players/cmd/stop` (2.3); restore re-asserts mute/volume only (2.3, 2.4). Architecture: core package + boundary + packaging (1.1); ports (1.5); adapters (2.1–2.4); contract JSON (2.5). Signal (1.2), capture (4.1), estimation (1.3), planning (1.4), session (1.5), API (2.5), events (2.4, 3.1), UX screens (3.2, 3.3, 4.2, 4.3), failure handling (4.3), persistence (2.1, 2.4), security (2.2, 2.5), testing lists (each task), stages 0–5 (0.1, 5.x). Risks: ≥ 2000 ms warning (1.4), measurement age (3.2 `last_measured_at` — set by the use case on apply: Task 2.5 `apply` stamps `last_measured_at` on the group cache; add that line when implementing `apply`), phone browsers (4.1 iOS order of operations; 4.3 manual check). Gap found and closed: `last_measured_at` origin was unspecified — it is stored in memory per `(group, member)` by `application/sync_tuning.py` on apply and merged into `SyncMember` (not persisted; the spec's "age" is per bridge run).

**Placeholder scan.** No "TBD/TODO"; the only "as above"/"as specified" references point to code or lists within the same task. Stage 0 scratch code is intentionally throwaway and not committed.

**Type consistency.** `Member.ma_player_id` is the key everywhere (`Measurement.member`, `ProposalRow.member`, `Snapshot.*` maps, API `member=` query, UI `ma_player_id`). `SyncSession.to_dict()` keys match `SyncSessionOut` fields (`undo_until_s`, `signal_running`, `soloed`, `reference`, `round`, `history`). `ControlError.code` values match `_STATUS` and the spec's error list; `MaApiError.code ∈ {ma_unavailable, ma_forbidden, ma_error}` is wrapped unchanged. `ClickTrackHost.url_for(session_id)` / `release(session_id)` are the same in `ports.py`, `click_track_host.py` and the fakes. `SessionStore.create(group_id, mode, point)` matches the use case call in 2.5.

**Review Focus pins.** 1 → `test_members_resolve_origin_adjustability_and_delay`, `test_not_adjustable_and_failed_members_are_left_out_and_listed`, `test_manual_delay_write_refuses_a_non_adjustable_member`. 2 → `test_forbidden_write_becomes_ma_forbidden`, `test_apply_failure_keeps_the_proposal…`, `test_forbidden_apply_keeps_the_proposal`, `StepProposal` copy test. 3 → `test_silence_is_not_heard_and_garbage_is_invalid`, `test_garbage_recordings_are_400_not_500`. 4 → `test_restore_persisted_after_restart_stops_signal_and_unmutes`, `test_restore_persisted_keeps_the_file_when_ma_is_down`, `test_end_restores_with_backoff…`. 5 → `test_lag_near_cycle_edge…`, `test_lag_unwraps_across_the_cycle_edge`, `test_clamping_is_marked_and_residual_predicted_with_warning`.
