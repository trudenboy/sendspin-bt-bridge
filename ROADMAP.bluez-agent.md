# BlueZ native-agent roadmap

Forward work on top of the `services/pairing_agent.PairingAgent`
introduced in v2.62.0-rc.1. Tactical (per-device, per-class) — separate
track from the strategic v3 roadmap in `ROADMAP.md`.

Sorted by priority. Items #1–#3 shipped in v2.62.0-rc.1 proper; the
items below are the deferred follow-ups.

---

## #4 — UI passkey confirmation modal (real SSP Numeric Comparison)

**Status:** deferred to 2.63+.

Today `RequestConfirmation` auto-confirms silently. Secure SSP Numeric
Comparison requires the user to compare the passkey shown on the
speaker against the one shown on the bridge UI and click Yes/No.

**Design sketch:**

1. Agent gets passkey → emits SSE event
   `{"type": "pair_confirmation", "device": mac, "passkey": int}`.
2. UI renders a modal: "Does this speaker show the code `941189`?".
3. User clicks Yes/No → `POST /api/bt/pair_confirm` → async `Event`
   set in the agent → method returns normally (yes) or
   `raise DBusError("org.bluez.Error.Rejected")` (no).
4. BlueZ agent default timeout is 30 s; keep the modal visible for
   ~25 s and auto-fall-back to current behaviour afterwards.

Use cases expanded:
- Cars and smart TVs that refuse "too-fast" auto-yes.
- Security-sensitive deployments where operator wants explicit consent
  per pairing.

Keep the current silent auto-yes as the default for headless HA.
Make the modal opt-in via a new config flag `INTERACTIVE_PAIR_CONFIRM`.

---

## #5 — "Pair with PIN" UI flow

**Status:** deferred to 2.63+.

`PairingAgent.RequestPinCode` currently returns the PIN from
`COMMON_BT_PAIR_PINS` (`0000, 1234, 1111, 8888, 1212, 9999`).
Devices with a custom PIN printed on a sticker (1990s–2010s car
hands-free kits, a few medical bridges) never pair because their PIN
isn't in the popular list.

**Design sketch:**

1. New input field in the scan modal: "Device PIN (if required)".
2. `POST /api/bt/pair_new` already accepts optional params — add
   `pin` (string, 4–6 digits). Validate server-side, pass through
   to `_run_standalone_pair_inner` which already forwards `pin` to
   `PairingAgent(pin=<user_input>)`.
3. If pair fails with PIN rejection, surface a retry prompt in the
   UI showing the attempted PIN.

~50 lines scan modal + 10 in agent. Minimal risk — adds one
explicit-PIN path without touching the popular-PIN retry loop.

---

## #6 — DisplayPasskey / DisplayPinCode → push to UI

**Status:** deferred to 2.63+.

When the peer has `DisplayOnly` capability and we're `KeyboardDisplay`,
BlueZ calls `Agent.DisplayPasskey(device, passkey, entered)` or
`Agent.DisplayPinCode(device, pincode)` to tell us the code the peer
just generated and showed to the user. Currently we only log it.

**Design sketch:**

1. Agent forwards passkey to SSE (same channel as #4).
2. UI renders a modal: "Your speaker is asking to enter the code
   `123456` — type it on the speaker now."
3. Works with `entered` parameter (incrementing counter as user types
   on the peer) for live feedback.

Expands support for **DisplayOnly speakers with dynamic PINs**
(legacy hands-free kits, cheap BLE speakers, some 2000s car DSP
heads). These are entirely unsupported today.

---

## #7 — Per-device capability override

**Status:** deferred to 2.63+.

Today the capability choice is global via
`EXPERIMENTAL_PAIR_JUST_WORKS`: all-or-nothing.

**Design sketch:**

Add optional `pair_capability` field to each `BLUETOOTH_DEVICES`
entry: `"DisplayYesNo" | "NoInputNoOutput" | "KeyboardDisplay" | "DisplayOnly" | "KeyboardOnly"`.

`_run_standalone_pair_inner` reads the per-device override before
falling back to the global flag.

Use case: fleet with 5 speakers where 4 need `DisplayYesNo` (Numeric
Comparison) and 1 ancient unit needs `NoInputNoOutput` (Just Works)
— current global switch forces a bad trade-off.

**Integration:** needs config schema bump (`CONFIG_SCHEMA_VERSION`)
since the field is new in `BLUETOOTH_DEVICES`.

---

## #8 — Full D-Bus pair pipeline, retire `bluetoothctl` subprocess

**Status:** deferred to 2.64 / 2.65 (multi-PR effort).

The biggest architectural win. Current pair pipeline goes through
`subprocess.Popen(["bluetoothctl"])`, parsing stdout for state
transitions. Every step is a race window.

BlueZ exposes the same functionality on D-Bus:
- `org.bluez.Adapter1.StartDiscovery` + `PropertiesChanged` signals
- `org.bluez.Device1.Pair()` — direct async method
- `org.bluez.Device1.Connect()` — direct async method
- `org.bluez.Device1.CancelPairing()` — for a Cancel button

Combined with our native agent on `AgentManager1.RegisterAgent`, we
get:
- Zero race conditions (signals vs stdout parsing)
- Typed diagnostics (`org.bluez.Error.*` error names vs regex on
  human-readable strings)
- Full timing control (no guessing `_PAIR_SCAN_DURATION`)
- Clean cancellation
- Foundation for #4, #7, #9, #10

**Sketch:**

1. New module `services/bluez_dbus.py` — `Adapter`, `Device`,
   `Discovery` async wrappers around dbus-fast proxies.
2. Port `_run_standalone_pair_inner` to use it; keep old
   subprocess-path gated behind `EXPERIMENTAL_LEGACY_PAIR_SHELL`
   flag for rollback.
3. Port the other two pair sites (monitor reconnect, reset &
   reconnect) once standalone is proven in production.
4. Eventually drop the flag and the subprocess path.

~300 LoC, weeks of bake-in before full rollout.

---

## #9 — BLE / LE Audio support

**Status:** depends on #8.

`PairingAgent` (Agent1) already works for both BR/EDR and LE. The
missing piece is the pair pipeline — `scan bredr` in the current
shell-based path excludes LE-only advertisers. Once #8 lands, we can
use `scan dual` or explicit LE discovery via D-Bus and support:

- LE Audio-compatible buds / speakers (emerging category)
- Classic devices with LESC (Secure Connections with Numeric
  Comparison or Passkey Entry)
- GATT-based A2DP v2 once available

Gating on #8 because LE support inside the current shell-pipeline
is brittle (`bluetoothctl` LE support is not first-class).

---

## #10 — Structured pair trace in recovery timeline

**Status:** can partially land alongside #3 telemetry; full version
depends on #8.

New `services/pair_trace.py` that records every agent invocation
structurally — timing, method name, peer capability (read from
`Device1.IOCapability` at call time), passkey/PIN, outcome — and
merges into `services/recovery_timeline.py`.

Value: support-triage in a single `GET /api/pair/trace/<mac>` call
instead of "please attach a DEBUG log and tell us approximately
when you clicked Pair".

---

## #2 scope — `AuthorizeService`: whitelist expansion

**Status:** minimum shipped in 2.62.0-rc.1.

Current whitelist is audio-centric. Future candidates for addition
when concrete devices hit the reject path:

- **Battery Service** (`0000180F-...`) for devices exposing battery
  reporting as a standalone service rather than profile-embedded.
- **Device Information Service** (`0000180A-...`) — already advertised
  universally but some devices request authorization separately.
- **HID-over-GATT** for Bluetooth remotes attached to speakers
  (selection of keypad keys).

Add on first concrete issue where a legitimate audio device gets
`Rejected`. Log entries already surface rejected UUIDs for triage.

---

## Devices currently unsupported — expected unblocks

| Device class | Today | Unblocked by |
|---|---|---|
| Numeric Comparison + strict peer (Synergy 65 S) | Fixed in 2.62.0-rc.1 | native agent default |
| Non-default PIN (2000s car stereos, HMDX JAM variants) | hardcoded popular-PIN list misses | #5 |
| DisplayOnly speakers with dynamic PIN | no way to surface peer-shown PIN | #6 |
| Strict service-authorization peer | over-broad auto-authorize | #2 scope expansion |
| LE Audio / LESC only | scan bredr excludes | #8 → #9 |
| Peer demanding two-sided confirm | silent auto-yes | #4 |
| Multi-profile phones preferring HFP over A2DP | over-broad authorize | #2 scope expansion |

---

## Non-goals

- TTS / voice announcements — belongs to strategic `ROADMAP.md`
  post-v3 block.
- HID remote-as-control — out of scope (security sensitivity, niche).
- Migration to `dbus-python` for agent — `dbus-fast` is the chosen
  async D-Bus stack and should stay consistent with `bt_monitor`.
