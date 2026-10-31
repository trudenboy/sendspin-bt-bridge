# Hardware section: staged plan (E1–E5)

Status: ready to execute (2026-10-31). Design:
[`2026-10-31-hardware-section-design.md`](2026-10-31-hardware-section-design.md).
ADR: [`0002`](../adr/0002-hardware-inventory.md).

Five vertical stages. Each ends with something visible in `DEMO_MODE=true` and
with its tests green; nothing merges on a "backend done, UI later" promise.

Every stage follows the same rules:

* **TDD**: test first, red, then the minimal green. Never edit an existing test
  to pass; if behaviour is deliberately replaced, the replacement is a new test
  and a `CHANGELOG.md` entry.
* **Layering**: router → `application` → `services`. No module-level `services`
  import in a router, no `api` import in `application`, no blocking call in a
  coroutine (the existing AST tests enforce the last one).
* **Contract**: `ui/openapi.json` and `ui/src/api/schema.d.ts` are regenerated
  in the same commit as the change; CI fails on drift.
* **Locale**: every new string has `en` and `ru`; `check:locales` must pass.
* **Actions**: every new button is registered in `ACTION_POLICY`; the action
  inventory test fails otherwise.
* **No new config key** in v1. If one becomes unavoidable, stop and re-open the
  design: it needs four edits (`DEFAULT_CONFIG`, `config/schema.json`,
  migration normalization, `config_diff` classification) plus an
  unchanged-save no-op test.

---

## E1 — Inventory and the page (read-only)

**Status (2026-10-31): backend and page delivered, two items deferred to E1b.**
Shipped: the reader with its TTL cache, `roles.py`, `capabilities.py`, the three
`/hardware` read routes, the `hardware` event type with a demand-driven producer,
`adapter_names` through `load_config`, demo fixtures and the demo install, the
SPA store with the SSE probe fallback, the page with its three states, the axe
and component tests. Deferred: removing `PairedDevicesList`/`BtDeviceInfoModal`
and the link from `AdaptersSection` — the page has to be seen working on a real
host before the old surfaces disappear. Until then both paths exist, which is
why E1b is a separate, small change.

**Scope.** The reader, the aggregate, the event type, the page with
**Controllers** and **Bluetooth devices**.

**Backend**

* `services/hardware/__init__.py`, `inventory.py` — one process-wide reader,
  2 s TTL, single in-flight lock, `as_of`, `stale`. One `GetManagedObjects` per
  adapter; `bluetoothctl` only as a lease-bound fallback.
* `services/hardware/capabilities.py` — `bluetooth`, `dbus`, `audio_server`,
  `capture`, `default_sink`, with reason codes.
* `services/hardware/roles.py` — pure `infer_roles(...)`.
* `application/hardware.py` — `snapshot()`, `controller(id)`,
  `bluetooth_device(mac)`; joins config (`player_id`, `area`) and builds
  `degraded[]`.
* `application/models/hardware.py` — `HardwareSnapshot`, `ControllerSummary`,
  `ControllerDetail`, `BtDeviceSummary`, `BtDeviceDetail`, `CapabilitySet`,
  `DegradedSection`, `RoleEvidence`.
* `api/routers/hardware.py` — `GET /hardware`, `GET /hardware/controllers/{id}`,
  `GET /hardware/bluetooth/{mac}`.
* `api/events.py` — `hardware` in `ALL_TYPES`, publisher coalesced to ≤1/s.
* `services/bluetooth/adapter_names.py` — read through `load_config()` instead
  of opening `config.json` directly (forced refactor: the page shows adapter
  names, and today they lag the config module's view).

**Frontend**

* `views/HardwareView.vue`, `components/hardware/{ControllerCard,BtDeviceCard,CapabilityBanner,EntityDrawer}.vue`,
  `stores/hardware.ts`, `api/hardware.ts`.
* `router/index.ts` (`/hardware`), `router/nav.ts` (fifth item, icon, short
  label), `router/segments.ts` (new first segment — ingress base-path detection
  breaks without it).
* `stores/bridge.ts` — the page no longer reads `adapters` from here; one owner
  of adapter state (the second, write-only store field is deleted).
* Remove `PairedDevicesList` and `BtDeviceInfoModal`; `AdaptersSection` keeps
  name/CoD editing and gains a link to the new page.
* i18n: `hardware.*` namespace in `en.json`/`ru.json`; the source-status strings
  stay until E3.

**Demo** — `demo/fixtures.py` grows `DEMO_CARDS`, `DEMO_SOURCES`,
`DEMO_BT_TRANSPORTS`; `demo/__init__.py` patches the new reader so the demo shows
two adapters, three paired devices and a phone.

**Tests** — `tests/unit/services/hardware/test_inventory.py` (FakeBluez +
`tests/fixtures/pactl/*.txt`), `test_roles.py` (table-driven), `test_capabilities.py`;
`tests/unit/application/test_hardware.py`; `tests/unit/api/test_hardware.py`
through `api_client` (200, 404 `entity_not_found`, 503 `hardware_unavailable`,
degraded section, `id_synthetic`); `tests/unit/api/test_events.py` keeps the
400-on-unknown-type case and gains a `hardware`-type case; UI:
`tests/unit/stores/hardware.spec.ts` (probe fails → poll), component tests for
empty/unavailable/stale, axe suite.

**Exit criteria.** With `DEMO_MODE=true` the page lists controllers and paired
devices; on a host without D-Bus it shows `unavailable` with a reason, not an
empty list; adapter power state refreshes without a page reload or an SSE
reconnect; axe passes.

---

## E2 — Audio: cards, ports, outputs and inputs

**Status (2026-10-31): delivered.** Read model, five safe commands and the audio
section of the page are in place; demo mode serves a real audio section. Review
found that on PipeWire every endpoint lost its card (`Owner Module: n/a`, no
`Sinks:` lists) — ownership now joins the sink's `device.name` to the card's
`Name`, with a real PipeWire fixture pinning it. A failed `pactl` read is
reported as unavailable rather than as an empty list, and a timed-out `wpctl`
no longer claims the phone is silent.

**Scope.** The audio half of the read model, plus the safe commands.

**Backend**

* `services/audio/sink_inventory.py` — `LocalCard`, `LocalSource`,
  `parse_pactl_cards`, `parse_pactl_sources`, `list_local_cards`,
  `list_local_sources`; keep the full `Ports:` map and each port's profile set
  (today only the active port survives).
* `services/hardware/inventory.py` — cards, sinks, sources, capture node
  discovery (`wpctl` nodes, `pactl` `bluez_source.*`).
* `application/audio.py` — `set_sink_volume`, `set_sink_mute`,
  `set_default_sink`, `suspend_sink`, `resume_sink`; all raise `UseCaseError`.
* `api/routers/audio.py` — the five command routes; `api/routers/hardware.py` —
  card/endpoint detail routes.
* `application/models/hardware.py` — `CardSummary/Detail`, `EndpointSummary/Detail`,
  `EndpointOwner`.

**Frontend**

* `components/hardware/{AudioCardPanel,EndpointRow,PortList}.vue`; audio section
  in `HardwareView`; drawer tabs for a card and an endpoint.
* Volume/mute/default/suspend actions with `ACTION_POLICY` entries; suspend
  gets a confirm (it silences an output).
* `AudioOutputPicker` keeps working but reads the new store; its list is no
  longer the only place outputs are visible.

**Tests** — parser tests from `tests/fixtures/pactl/*.txt` (including a card with
an unplugged port and a `bluez_card`), use-case tests for the five commands,
API tests for 400 `invalid_audio_name`, 404, 409 `not_supported_for_backend`;
UI tests for the three endpoint states; axe.

**Exit criteria.** An operator can tell from the page that an output is silent
because its active port is unavailable or the sink is suspended; volume and mute
round-trip; BT-derived sinks still do not appear in the local output list.

---

## E3 — Source feed

**Status (2026-10-31): delivered.** `application/source.py` carries the source
orchestration; `SourceFeed` is in the aggregate and the phone gets an `input`
role with `source_feed:<MAC>` evidence; the settings row moved to the hardware
page as a strip with four states. Review found that `POST /bridge/source`
returned 200 on a host that cannot capture, and that a failed pairing window
painted a streaming source as `error` — both fixed (`422 capture_unavailable`,
`pairing_error` kept separate).

**Scope.** The phone→bridge source becomes an entity, and the settings row moves.

**Backend**

* `application/source.py` — **new**; takes over the orchestration currently
  inlined in `api/routers/bridge.py:100-145` (`start/stop`, `players`, pairing
  window). The routes stay at the same paths, now one line each.
* `services/source` state read through a thin, typed accessor instead of two
  module-level dicts read from other threads.
* `application/models/hardware.py` — `SourceFeed`; `services/hardware/inventory.py`
  joins it and sets `roles += input` on the phone's device.

**Frontend**

* `components/hardware/SourceFeedPanel.vue` (the strip): enabled, state, phone,
  capture kind, relays, start/stop, pairing window.
* `SourceStatusSection` leaves Settings → Music Assistant; a link remains there.
* Errors are no longer swallowed: a failed call shows an inline error with the
  server's `code`.

**Tests** — `tests/unit/application/test_source.py` (start/stop idempotence,
pairing window, relay arming, `capture_unavailable`); API tests that the old
paths still answer identically (no contract break); UI tests for the four
states; axe.

**Exit criteria.** `attached_devices` is visible on the page; with a phone
streaming, the device card shows an `input` badge with evidence; on a host
without PipeWire the capture row says why instead of disappearing.

---

## E4 — Bluetooth writes

**Status (2026-10-31): delivered.** Connect, disconnect, add-as-speaker, forget
bond and pairing are on the page. Forgetting a bond a player depends on is
refused with `bond_in_use_by_player` (player id + remediation, no `force`).
Review caught a lease-ownership defect: the lease was released from the event
loop, so a cancelled request could free it while `bluetoothctl connect` was still
driving the controller; the verb now runs and releases in `bt_executor()`.

**Scope.** Pair, unpair, connect, disconnect from the page.

**Backend**

* Reuse existing endpoints; add only `bond_in_use_by_player` (409 with
  `remediation` and the player id) when forgetting a bond that backs a player.
* `application/devices.py` — expose the player lookup as a use case, not a
  router-side join.

**Frontend**

* Actions on the device card and drawer: connect, disconnect, forget, pair
  (opens the existing scan flow), with `ConfirmConsequences` naming the affected
  player.
* After pairing, the page refreshes its inventory instead of waiting for the
  next event.

**Tests** — API: forgetting a bond used by a player is 409 and names the player;
forgetting a free bond is 204; connect/disconnect map refusals to `UseCaseError`
codes. UI: action inventory, confirm bodies contain the player name.

**Exit criteria.** The page covers the full bond lifecycle; no destructive
action runs without a named consequence.

---

## E5 — Documentation

**Status (2026-10-31): delivered.** The docs-site page is published in both
languages with a sidebar entry, the glossary carries Hardware/Оборудование and
Audio input/Аудиовход, `CONTEXT.md` carries Card, Audio endpoint, Link role and
Source feed, ADR-0002 is accepted, and the changelog has the user-visible entry.
Screenshots are listed in `SCREENSHOTS_TO_RETAKE.md` but not yet captured: they
need a running demo instance and a browser, which this environment does not
have.

Acceptance evidence for the whole section: `uv run pytest tests/unit` — 6646
passed; `NODE_ENV=test npm test` — 1697 passed; `npm run build` and the docs-site
build are clean; `ruff` over `src tests demo scripts custom_components`, `mypy`
(244 files), `export_openapi.py --check` and `lint_changelog.py` are clean.
`DEMO_MODE=true` now starts (it had been broken since the v3 rewrite, which is
why no screenshot stand existed) and serves `GET /api/v1/hardware` with 3
controllers, 9 paired devices, 9 cards, 9 sinks, 1 source and no degraded
sections.

* `docs/adr/0002-hardware-inventory.md` (accepted).
* `CONTEXT.md` — **Card**, **Audio endpoint**, **Link role**, **Source feed**.
* `docs/ux-glossary.md` — **Hardware / Оборудование**, **Audio input / Аудиовход**.
* `docs-site/src/content/docs/hardware.mdx` + `ru/hardware.mdx` — the drafts
  already exist with `draft: true`; E5 drops the flag, adds the sidebar entry
  with `translations: { ru: 'Оборудование' }` and the cross-links from
  `devices.md`, `audio-outputs.md`, `bluetooth-adapters.md`. The pages stay
  drafts until the section ships: documenting UI that does not exist yet would
  publish a lie.
* Screenshots captured from `DEMO_MODE=true`, listed in
  `docs-site/src/assets/SCREENSHOTS_TO_RETAKE.md`.

**Exit criteria.** `cd docs-site && npm ci && npm run build` passes; every new
UI word appears in the glossary; the docs page describes the page as shipped.

---

## Acceptance criteria

1. `DEMO_MODE=true` renders the page with 2 controllers, paired devices, ≥2
   local outputs and ≥1 input; screenshots updated.
2. A host without D-Bus shows the section `unavailable` with a reason — never an
   empty list, never a 500.
3. A host without PipeWire marks the phone capture `pipewire_required` rather
   than hiding it.
4. New UI against an old backend: the event stream survives, the page falls back
   to polling.
5. Adapter and `powered` state refresh without a reload or an SSE reconnect
   (today's bug), pinned by a test.
6. Every refusal is a `UseCaseError` with a stable `code`; each new code has a
   test.
7. axe suite for the page and the entity drawer: zero violations; `ru`/`en`
   parity; the new glossary term is covered by its lint.
8. CI green end to end: `ruff check`, `ruff format --check`, `mypy`,
   `pytest --cov-fail-under=50`, `export_openapi.py --check`, `check:api`,
   `check:locales`, `npm test`, `npm run build`, docs-site build.

## Verification commands

```bash
uv run pytest tests/unit/services/hardware tests/unit/application/test_hardware.py -q
uv run pytest tests/unit/api/test_hardware.py tests/unit/api/test_events.py -q
uv run ruff check . && uv run ruff format --check .
uv run mypy --config-file pyproject.toml src/sendspin_bridge/
uv run python scripts/export_openapi.py --check
cd ui && npm run check:api && npm test && npm run build
cd docs-site && npm ci && npm run build
```

## Risks

| Risk | Mitigation |
|---|---|
| Lease contention: a `bluetoothctl` fallback read starves pairing | reads take the lease only on the fallback path; test with `fake_lease` |
| Slow hosts: 10 devices × D-Bus × `pactl` | one `GetManagedObjects` per adapter, one TTL cache, no per-entity reads |
| PA/PipeWire naming drift | one grammar (`sink_names`); no string comparisons in new code |
| Multi-adapter bonds | `controller_macs[]`, never a single field |
| Synthetic `hciN` when sysfs is absent | `id_synthetic: true`; commands take MAC |
| Demo drifts from real parsers | demo fixtures are parsed by the real parsers in tests |
| SSE skew kills the stream | capability probe, covered by a UI test |
| Coverage floor | tests land with each stage, not after |

## Review order

1. E1 read model and contract (the shape everything else depends on).
2. E2 audio entities and commands.
3. E3 source extraction (the layering fix).
4. E4 write surface and destructive-action copy.
5. E5 docs.
