# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.65.0-rc.5] - 2026-04-28

### Fixed — Copilot review on PR #218 (v2.65.0-rc.4)

Five issues caught by the Copilot reviewer on the rc.4 PR:

- **Legacy availability topic stuck on stale value.**
  ``services/ha_mqtt_publisher.py`` only published the legacy
  ``sendspin/<pid>/availability`` topic on device removal — the full /
  delta paths skipped it.  HA caches from rc.1–rc.3 that still
  subscribe to the legacy topic kept the last retained value forever.
  ``_publish_full_state`` and ``_publish_delta`` now mirror the runtime
  channel into the legacy topic so older caches stay in sync.
- **Disabled-device entities showed hard-coded defaults.**
  ``services/ha_state_projector._project_disabled_device`` synthesised
  ``idle_mode=default``, ``static_delay_ms=0``, etc., regardless of
  what the operator had saved — so HA displayed misleading values for
  disabled devices and a write-back from HA could silently overwrite
  the saved settings.  ``bridge_orchestrator.initialize_devices`` now
  enriches each ``disabled_devices`` entry with the saved per-device
  config knobs (``idle_mode``, ``keep_alive_method``,
  ``static_delay_ms``, ``power_save_delay_minutes``,
  ``bt_management_enabled``, ``preferred_format``, ``room_id``,
  ``room_name``); the projector uses what's there and falls back to
  defaults only for legacy entries that pre-date the enrichment.
- **``GET /api/ha/mosquitto/status`` exception path hid the banner.**
  The 500 fallback hardcoded ``available=False``, which made the UI
  silently drop the install banner inside HA addon mode — exactly when
  the operator most needed actionable hints.  Now derives ``available``
  from ``SUPERVISOR_TOKEN`` even on the error branch and reuses the
  ``MOSQUITTO_ADDON_SLUG`` / ``MOSQUITTO_ADDON_DEEP_LINK`` constants
  from ``services.ha_addon`` instead of duplicating them.
- **Runtime availability ignored daemon liveness.**
  ``availability_runtime`` only mirrored ``device.bluetooth_connected``,
  so a dead daemon subprocess with BlueZ still reporting the link up
  would leave RSSI / battery / audio_format reading the last cached
  value forever.  Now gated on ``device.connected AND
  device.bluetooth_connected`` — if either is false, runtime entities
  go ``unavailable``.
- **Legacy ``enabled=false, mode=mqtt`` auto-enabled on UI load.**
  ``static/app.js:_populateHaIntegrationForm`` populated the dropdown
  from ``block.mode`` regardless of ``block.enabled``, so a saved
  config where the operator had toggled the master off but left the
  transport at ``mqtt`` would silently re-enable on the first save
  through the new UI.  Now treats ``enabled=false`` as off in *both*
  directions: ``enabled=true, mode=off`` stays off, and
  ``enabled=false, mode=mqtt|rest`` is also forced off — the operator
  must explicitly pick a transport to opt back in.

## [2.65.0-rc.4] - 2026-04-28

### Changed — HA: fleet-based visibility + per-class entity availability

Standby and disabled devices were effectively invisible from HA: the
projection only iterated active clients with a live BT link, and any
device with ``bluetooth_connected=false`` reported ``offline`` on its
single availability topic — which dragged every entity (including
toggles and command buttons) to ``unavailable``.  Net result: an
operator could not flip ``enabled`` on a disabled device or fire
``wake`` on a standby device from HA, defeating the whole point of
the integration for fleet management.

This release reworks visibility around two ideas:

1. **Fleet-based exposure.** ``project_snapshot`` now also iterates
   ``snapshot.disabled_devices``.  Every member of the fleet
   (configured in ``BLUETOOTH_DEVICES``, regardless of ``enabled``
   flag) appears in HA with synthesised entity states (``enabled``
   shows ``False``, runtime fields default to safe falsy values,
   config knobs preserve their saved settings).  HA operators can
   re-enable a disabled device from the standard ``switch.<player>_enabled``
   entity, which the dispatcher routes to ``bt_commands.apply_device_enabled``.

2. **Per-class availability** via a new ``EntitySpec.availability_class``
   field.  Three classes:
   - ``config``: online whenever the device is in the fleet.  Used
     for the ``enabled`` switch, all command buttons (``reconnect``,
     ``wake``, ``standby``, ``power_save_toggle``, ``claim_audio``),
     and every config knob (``idle_mode``, ``static_delay_ms``, etc.).
   - ``runtime``: online only when the BT link is up.  Used for
     live diagnostics whose values are meaningless when the speaker
     is unreachable (``rssi_dbm``, ``battery_level``, ``audio_format``,
     ``audio_streaming``, ``reanchoring``).
   - ``cumulative``: shares the ``config`` availability gate so HA
     dashboards keep showing last-known values through standby.
     Used for counters and last-error fields (``reanchor_count``,
     ``last_error``, ``health_state``, ``bt_standby``, ``bt_power_save``).

   MQTT discovery payloads now route each entity to one of two
   per-device availability topics: ``sendspin/<pid>/availability/config``
   and ``sendspin/<pid>/availability/runtime``.  The legacy
   ``sendspin/<pid>/availability`` topic is still published (and now
   tracks runtime) for backwards compat with rc.1–rc.3 HA caches.

   The HACS custom_component coordinator gains parallel
   ``device_runtime_available`` / ``device_config_available`` /
   ``device_lifecycle`` accessors; the entity base picks the right
   one via ``availability_class``.

Side benefit: standby devices now show ``device_lifecycle="standby"``
and disabled devices show ``"disabled"`` in the projection JSON, so HA
dashboards can highlight parked / dormant speakers without parsing
multiple binary sensors.

### Changed — HA integration panel: dropdown-master + connected banner + Mosquitto guidance

The HA integration tab (Settings → Home Assistant) gets a UX rebuild
that mirrors the Music Assistant panel pattern:

  - **Dropdown replaces toggle.** The "Enable HA integration" checkbox
    is gone; the existing transport dropdown (off / mqtt / rest) is
    now the single source of truth.  Picking a transport implicitly
    enables the integration, picking ``off`` disables it — no two-step
    flow where the master switch can disagree with the picked
    transport.  Saved configs with an inconsistent ``enabled=true,
    mode=off`` (or vice versa) load as ``off`` in the new dropdown so
    the user makes the choice explicit on first save.
  - **Connection-status banner.** A new card at the top of the panel
    shows "Off / Connecting / Connected via MQTT to <broker> /
    Configured for REST but not connected yet / Error: …" with the
    same colour palette the MA panel uses.  Once the publisher is
    ``connected`` the transport-specific cards collapse; the operator
    sees only the banner with a "Reconfigure" button to expand the
    form again.
  - **Mosquitto guidance banner.** When in HA addon mode, the panel
    detects the Mosquitto broker add-on state via the new
    ``GET /api/ha/mosquitto/status`` endpoint:
      * mode == ``mqtt`` and the add-on is missing or stopped →
        shows install/start instructions with a deep-link to the
        official Mosquitto add-on page (Supervisor add-on install
        needs ``manager`` role, which our add-on doesn't request, so
        the link rather than a one-click install is the safe path).
      * mode == ``off`` and the add-on is installed and started →
        offers a "Set up automatically" CTA that flips the dropdown
        to ``mqtt``, leaves the broker on ``auto`` (Supervisor
        auto-resolves credentials), saves, and hot-applies — one
        click from "off" to "connected" without typing anything.

The auto-config CTA is gated on the form being clean (no other
unsaved changes elsewhere on the page) so saving doesn't silently
flush unrelated dirty state.

### Fixed — HA "Claim audio" no longer breaks playback

``services.bt_commands.command_claim_audio`` was probing for a
non-existent ``MprisPlayer.assert_active_source`` attribute and falling
through to ``command_reconnect`` on every press — so pressing the
``button.<player>_claim_audio`` entity from HA tore the BT link down
and back up instead of doing the lightweight MPRIS-source assert that
the bridge web UI's Claim Audio button does.  The dispatcher path now
mirrors ``POST /api/bt/claim/<mac>``: it looks up the player in
``MprisRegistry`` and schedules ``set_playback_status("Playing")`` on
the asyncio loop, which BlueZ propagates over AVRCP to the speaker.
Returns 409 instead of silently reconnecting when the speaker has no
active MPRIS player (i.e. is not currently connected).

### Removed — `reset_reconnect` button from HA catalog

Heavy recovery action better triggered manually from the bridge web UI
than from an HA automation that might fire it on a transient blip.
Catalog reduces from 26 → 25 per-device entities.  Bridge web UI
keeps the button.

## [2.65.0-rc.3] - 2026-04-28

### Changed — auth-gate read switched to `current_app.config`

``_require_authenticated_session`` and ``_validate_csrf_token`` in
``routes/auth.py`` previously reached for ``web_interface._auth_enabled``
(private global, hard import dependency, swallowed broad
``except Exception``).  They now read ``current_app.config["AUTH_ENABLED"]``
to mirror the pattern used in ``routes/views.py`` / ``routes/api_status.py``,
which keeps the blueprint decoupled from the app entrypoint module
and lets test fixtures set ``app.config["AUTH_ENABLED"]`` directly
instead of monkeypatching a private global.  Caught by Copilot review
on PR #216.

### Changed — dropped dead `mode == "both"` branch in mDNS gating

``bridge_orchestrator._start_mdns_advertiser`` checked
``mode in ("rest", "both")`` "for the upgrade window", but
``load_config()`` already runs the migration that coerces ``"both"``
→ ``"mqtt"`` before this code sees it.  Simplified to
``mode == "rest"`` and updated the comment.  Caught by Copilot
review on PR #216.

### Removed — `pair` (per device) and `scan` (bridge-level) HA buttons

Both buttons were noise rather than signal in HA's automation
context.  ``pair`` is a one-shot interactive workflow — the speaker
must be in pairing mode at the right moment, and the bridge UI's
pair-flow modal is the right surface for it; pressing it from an
HA automation has no safe failure path.  ``scan`` returns a list
of nearby BT devices that's only meaningful inside the same modal,
so triggering it from HA produces no observable effect.  The bridge
web UI keeps both controls.  Catalog reduces from 28 → 27 per-device
entities and 7 → 6 bridge entities.

### Added — Settings → Home Assistant: conditional fields by transport

The Settings → Home Assistant tab now hides fields that aren't
relevant to the chosen transport, mirroring the rest of the form.
``off`` shows only the master toggle + transport selector;
``mqtt`` adds the broker card; ``rest`` adds the mDNS / Supervisor-pair
card and the bearer-token list.  No more typing into a card whose
fields the publisher won't read.

### Removed — `HA_INTEGRATION.mode = "both"`

``both`` shipped briefly in rc.1 / rc.2 but was a footgun: running
the MQTT publisher AND the REST custom_component path against the
same HA produced **two copies of every entity**.  Both transports
publish ``sensor.<player>_rssi``, ``button.<player>_reconnect``,
etc. with the same ``unique_id``; HA's entity registry keys on
``(domain, platform, unique_id)`` and the platforms differ
(``mqtt`` vs ``sendspin_bridge``), so the second set lands as
``sensor.<player>_rssi_2``.  The mode selector now offers
``off / mqtt / rest`` only.  Saved configs still containing
``"both"`` are normalised to ``"mqtt"`` on load (config_migration
``_normalize_ha_integration``) so an upgrade can't silently disable
publishing — operators who actually wanted REST flip the mode back
manually after upgrading.  Tests:
``test_ha_integration_mode_migration.py`` and
``test_resolve_mqtt_config_legacy_both_mode_normalised_to_mqtt``.

### Fixed — Settings tab redirected to /login on Docker / standalone (no-auth) deployments after upgrading to v2.65.0-rc.2

The new Settings → Home Assistant tab calls
``GET /api/auth/tokens`` on every config load to populate the bearer
token list.  The endpoint required an authenticated session
unconditionally, including when the global ``AUTH_ENABLED`` toggle is
off (Docker / standalone default) — and the JS 401 fallback then
bounced the browser to ``/login``.  The result was that operators
who had auth disabled were forced to a login page after upgrading
even though the toggle was still off.

The token endpoints (and the CSRF guard they share with the rest of
the auth blueprint) now treat "global auth gate off" as
"requirement satisfied", so standalone/Docker deployments without
``AUTH_ENABLED`` keep working unchanged.  HA addon mode is
unaffected (Supervisor sessions are always present).  Regression
test in ``test_auth_token_routes.py::test_token_endpoints_open_when_global_auth_disabled``.

### Fixed — MQTT broker auto-detect failed on HAOS

The HA addon manifest didn't declare ``services: ['mqtt:want']``,
so Supervisor refused queries to ``/services/mqtt`` with
``"No access to mqtt service!"`` and the bridge's
``ha_integration.mqtt_broker = "auto"`` path silently fell through
to "publisher disabled".  All three addon variants (stable / RC /
beta) now declare the optional service so the auto-detect populates
host / port / username from the Mosquitto add-on without the
operator typing them manually.  ``mqtt:want`` (not ``mqtt:need``)
keeps the addon installable when no MQTT broker is present.

### Changed — explicit clear path for the MQTT broker password

The Settings → Home Assistant tab now distinguishes "I didn't touch
this field" from "I want to drop the password".  When the form loads
the password input is pre-populated with the marker
``***REDACTED***`` (instead of staying empty); leaving the marker in
place keeps the existing password, while clearing the field saves an
empty value so an operator can switch a broker from auth → no-auth
from the UI without hand-editing ``config.json``.  ``None`` and
whitespace-only payloads are still treated as "untouched" so a
clumsy client can't accidentally clear the password.  Caught by
Copilot review on PR #215.

### Changed — reuse existing `escHtml()` helper in HA token list

The HA token list rendering used a private duplicate of the
HTML-escape helper.  Now uses the project-wide ``escHtml()`` from
``static/app.js`` for consistency with every other rendered string.
Caught by Copilot review on PR #215.

### Fixed — comment in `_readHaIntegrationFromForm` matches reality

The inline comment claimed the bridge's "keep existing" semantics
lived in ``translate_ha_config`` / ``config_diff``; the real merge
happens in the ``POST /api/config`` handler in ``routes/api_config.py``.
Updated the comment so future maintainers debug round-trips at the
right layer.  Caught by Copilot review on PR #215.

## [2.65.0-rc.2] - 2026-04-28

### Added — Settings → Home Assistant tab

The HA-integration controls promised by v2.65.0-rc.1's plan now have a
dedicated UI surface: open the Configuration drawer and switch to the
**Home Assistant** tab.  From here operators can:

- Toggle the master ``HA_INTEGRATION.enabled`` flag and pick
  ``mode`` (``off`` / ``mqtt`` / ``rest`` / ``both``).
- Auto-detect the HAOS Mosquitto add-on (one click; populates broker
  host / port / username / TLS).
- Edit broker URL / port / username / password / discovery prefix /
  TLS for self-hosted brokers.
- Toggle mDNS advertisement and Supervisor pair acceptance for the
  REST + custom_component path.
- See live publisher state (idle / connected / error) and discovery
  payload count.
- Mint long-lived bearer tokens for the custom_component (label,
  reveal-once, copy button); list issued tokens and revoke any of
  them in place.

The MQTT password masking pattern matches Music Assistant's:
``GET /api/config`` reports ``***REDACTED***`` whenever a password is
set (and an empty string when none), and a POST that echoes back the
redacted marker preserves the existing password instead of clearing
it.  ``AUTH_TOKENS`` is dropped from the GET payload entirely; the UI
fetches it from ``/api/auth/tokens`` so the bearer-token list never
flows through the config form.

Round-trip tests in ``tests/test_api_config_ha_integration.py`` cover
the redaction, password preservation, explicit overwrite, and the
per-config-key download sanitization.

### Fixed — HA custom_component pairing flow on HAOS was unreachable

The HA bootstrap endpoint ``POST /api/auth/ha-pair`` exists to mint a
long-lived bearer token for the custom_component on HAOS without
operator input — but the auth middleware in ``web_interface.py`` saw
it as a regular ``/api/*`` route and refused with 401 because no
token / session existed yet.  Symptom: HACS install on HAOS would
discover the bridge via mDNS, click "Configure", and silently fail
to pair.  ``/api/auth/ha-pair`` is now in ``_PUBLIC_PATHS`` so it
reaches the real gate (Supervisor-IP + ``X-Ingress-Path`` check in
``routes/auth.py``).  A regression test
(``test_auth_enforcement.py::test_ha_pair_is_public_pre_auth``) and
a lockstep test against ``_PUBLIC_PATHS`` keep this from regressing.
Caught by Copilot review on PR #214.

### Fixed — `find_client_by_player_id` lookup matches its docstring

The HA-side command dispatcher's ``find_client_by_player_id`` helper
promised case-insensitive comparison in its docstring but only did
exact-string matching.  Canonical bridge ``player_id`` values are
lowercase UUID5 strings, but HA discovery payloads round-trip them
through JSON / templates where a stray uppercase normalisation
upstream could silently misroute commands.  Both sides now ``casefold``
before comparing, with a regression test covering mixed case +
whitespace.  Caught by Copilot review on PR #214.

## [2.65.0-rc.1] - 2026-04-28

### Added — Home Assistant integration (issue #205)

Two parallel transports ship together for direct HA control of the
bridge from dashboards and automations:

**MQTT discovery (Path B).**  Enable in the new "Home Assistant"
config block; on HAOS the broker is auto-detected via the official
Mosquitto add-on.  The bridge publishes per-speaker entities under
`homeassistant/<component>/sendspin_<player_id>/...` with
`device.connections=[("bluetooth", mac)]` so HA's device registry
**merges them into the same device card** that Music Assistant's
integration already created for the speaker.  Result: one ENEBY20
device card with `media_player.eneby20` (from MA) plus our
`sensor.eneby20_rssi`, `sensor.eneby20_battery`, `binary_sensor.eneby20_streaming`,
`button.eneby20_reconnect`, `select.eneby20_idle_mode`,
`number.eneby20_static_delay`, etc.

**custom_component over REST + SSE (Path A1).**  Same repo ships
`custom_components/sendspin_bridge/` for HACS users.  HA's Zeroconf
discovers the bridge's `_sendspin-bridge._tcp.local.` advertisement;
on HAOS the integration auto-pairs via Supervisor, off-HAOS the user
pastes a token they generate in the bridge web UI.  REST + SSE
(`/api/ha/state`, `/api/status/events`) drive a `DataUpdateCoordinator`.

**MA-deduplication contract.**  Neither transport exposes
`media_player`, volume, mute, transport, queue, now-playing, or
group state — those are owned by Music Assistant's official HA
integration and would conflict if duplicated.  The bridge integration
stays strictly to BT diagnostics (RSSI / battery / streaming /
reanchor) and config knobs (idle mode / static delay / per-device
enabled / BT management).

### Added — Long-lived API bearer tokens

`POST /api/auth/tokens` mints PBKDF2-SHA256-hashed bearer tokens for
the HA custom_component.  `Authorization: Bearer <token>` is now
accepted on all `/api/*` routes alongside the existing session-cookie
auth.  HAOS users skip token generation entirely thanks to
`POST /api/auth/ha-pair`, which validates the Supervisor proxy chain
and mints a token without operator input.

### Added — `aiomqtt` dependency

The MQTT publisher uses `aiomqtt>=2.0` (an asyncio wrapper over
`paho-mqtt`).  Both ship as wheels for amd64 / aarch64 / armv7.

## [2.63.2-rc.1] - 2026-04-27

### Review follow-ups (Copilot on PR #206)

- ``services/recovery_assistant._build_config_writable_issue`` now
  accepts an optional ``preflight`` parameter and is called with the
  already-collected payload from
  ``routes/api_status._build_recovery_assistant_payload`` — avoids
  rerunning the bluetoothctl + audio probes a second time per
  ``/api/status`` request.
- ``services/preflight_status._build_config_writable_payload`` no
  longer silently returns ``ok`` when ``$CONFIG_DIR`` doesn't exist;
  attempts ``mkdir(parents=True, exist_ok=True)`` and classifies any
  resulting ``OSError`` via ``collection_error_payload`` so non-
  container deployments without a pre-created config dir surface
  the issue instead of hiding it.
- ``routes/_helpers.config_write_error_response`` now derives the
  reported path from ``exc.filename`` (most accurate) with a fallback
  to ``config.CONFIG_DIR`` (live, monkey-patchable) instead of
  ``os.environ["CONFIG_DIR"]`` — keeps the response consistent with
  the actual config location across HA addon mode and tests.
- ``entrypoint.sh`` chown-failure ERROR line updated to say "fail
  with 500 responses because config cannot be persisted" instead of
  the now-misleading "return generic 500" — the new structured 500
  is exactly what Layer 2 ships.
- ``routes/ma_auth._save_ma_token_and_rediscover`` gains an explicit
  ``None | tuple[Response, int]`` return annotation so the new
  contract is grep-able.

One new test in ``tests/test_preflight_config_writable.py``
(mkdir-failure classification) + autouse fixture in
``tests/test_recovery_assistant.py`` to neutralise the global
preflight collector so existing tests don't surface a false-positive
``config_dir_not_writable`` card on dev machines.

### Fixed — defense in depth: detect & surface non-writable ``$CONFIG_DIR``

Issue [#190](https://github.com/trudenboy/sendspin-bt-bridge/issues/190)
spent significant time at "MA OAuth returns Internal Server Error" with
no actionable diagnostic — root cause was the bind-mount target left
as ``root:root`` while the bridge process runs as UID 1000, so the
first config write (``_save_ma_token_and_rediscover`` →
``update_config``) raised ``PermissionError`` and Flask defaulted to
the generic 500 HTML page.

Three layers of defense so the next operator gets a single-glance
diagnosis:

1. **Startup banner** — entrypoint now probes ``$CONFIG_DIR`` with
   touch/unlink as the *runtime* UID (via ``setpriv`` / ``gosu``) and
   adds a ``Config write: ✓ writable`` / ``✗ NOT writable`` row to the
   banner.  On failure, also emits an ``ERROR:`` line in journald with
   the exact ``chown`` command operators can copy verbatim.
2. **Actionable 500** — every Flask handler that writes to the config
   directory now wraps the write in ``try/except OSError`` and returns
   a structured JSON 500 with a ``remediation`` block (chown for
   ``EACCES``, remount for ``EROFS``, generic for the rest) instead of
   Flask's default ``Internal Server Error`` HTML.  Wrapped sites:
   ``_save_ma_token_and_rediscover`` (5 OAuth callers), ``POST
   /api/config``, ``POST /api/config/upload``, ``POST /api/set-password``,
   ``POST /api/settings/log_level``.
3. **Diagnostics surface** — ``services/preflight_status`` adds a
   ``config_writable`` slice (status / writable / config_dir / uid /
   remediation).  ``services/recovery_assistant`` reads it and renders
   a ``config_dir_not_writable`` recovery card with the chown command
   in the summary so it appears in the UI Diagnostics panel without
   operators reading container logs.  ``services/operator_check_runner``
   adds a ``config_writable`` re-runnable check so the "Re-run check"
   button flips the card green as soon as the operator runs the chown.
   ``services/guidance_issue_registry`` registers the new issue at
   priority 15 (between ``runtime_access`` and ``bluetooth``).

Helper: ``routes/_helpers.config_write_error_response(exc, context=...)``
builds the structured response.  Distinguishes ``EACCES`` /
``EROFS`` / unknown ``OSError`` so each gets the correct hint;
non-OSError exceptions still raise so real bugs aren't masked.

``docker-compose.yml`` gains a comment block above the ``/config``
volume mount documenting the pre-start ``chown -R 1000:1000`` step.

Tests: 5 + 4 + 5 + 2 + 2 new in
``tests/test_config_write_error_response.py``,
``tests/test_ma_auth_config_write.py``,
``tests/test_preflight_config_writable.py``,
``tests/test_operator_check_runner.py``, and
``tests/test_recovery_assistant.py``.  Full suite 1719 passing.

## [2.63.0-rc.9] - 2026-04-26

UX + diagnostics polish on top of the rc.8 RSSI work.  No protocol
or audio-pipeline changes — every fix here is either a UI rearrange,
a settings-persistence guard, or a bug-report payload addition.

### Added — Settings UI: experimental "Allow HFP / HSP profile" toggle

`ALLOW_HFP_PROFILE` was previously a config-only field — operators
had to hand-edit `/config/config.json` to enable it for HFP-only
headsets.  Now exposed in the new "Experimental features" card with
an explicit warning in the tooltip that most BT speakers and
headphones will collapse to an 8 kHz mono call codec when HSP/HFP is
permitted.  Persisted across HA addon restarts via the new
preservation list (see below).

### Changed — BT info modal shows full ``bluetoothctl info`` output

The BT info modal previously rendered a 9-field summary (Name,
Alias, MAC, Paired/Trusted/Connected/Bonded/Blocked, Class, Icon).
That dropped the ``UUID:`` lines which carry the load-bearing
diagnostic for "does this speaker actually advertise A2DP Sink"
(``0000110b``), AVRCP target/controller, etc. — the same data that
took an SSH round-trip to extract during issue #168 triage.

Modal now renders ``info.raw`` directly: the full line-by-line
bluetoothctl output (Class, Icon, Paired, Bonded, Trusted, Blocked,
Connected, LegacyPairing, every UUID with friendly name, Modalias).
Backend already collected all of this in ``raw``; the frontend just
needed to use it.  Bluetoothctl's piped-stdin noise (``Agent
registered``, ``[bluetooth]#`` prompts) is filtered client-side.
Modal width bumped 440 → 620 px to fit the long UUID lines.

One new test in ``tests/test_bt_info_adapter_awareness`` pins the
parser contract: every UUID, Modalias, Class, and LegacyPairing
line must appear in ``info["raw"]``.

### Changed — Settings UI: experimental flags moved to dedicated card

All five experimental toggles (A2DP sink recovery dance, Reload PA
BT module, Adapter auto-recovery, Live RSSI badge, Allow HFP / HSP)
moved out of the Connection-recovery card into a new "Experimental
features" card directly below it.  The card itself carries
`data-experimental` so the entire group hides when "Show
experimental features" is off — Connection recovery now contains
only the two production-stable inputs (BT check interval +
Auto-disable threshold), eliminating the cluttered mix of stable
and experimental rows it had since rc.2.

### Fixed — HA addon: web-UI-only settings now survive restart

In HA addon mode every restart ran `scripts/translate_ha_config.py`
which rebuilds `config.json` from the addon's `options.json`.  The
addon schema doesn't expose the `EXPERIMENTAL_*` family or several
auth / update / sync settings — those are managed only via the
bridge web UI (Settings → Show experimental features).  The
translator's preservation list missed them, so a single restart
silently rewrote each toggle to its default and operators saw the
controls "not save".

Now preserved across restart in addon mode:

- `EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE`,
  `EXPERIMENTAL_PA_MODULE_RELOAD`,
  `EXPERIMENTAL_PAIR_JUST_WORKS`,
  `EXPERIMENTAL_ADAPTER_AUTO_RECOVERY`,
  `EXPERIMENTAL_RSSI_BADGE` — the entire experimental-flags family.
- `AUTH_ENABLED`, `BRUTE_FORCE_PROTECTION` — auth toggles.
- `MA_WEBSOCKET_MONITOR` — MA real-time sync toggle.
- `AUTO_UPDATE`, `CHECK_UPDATES`, `SMOOTH_RESTART` — update / restart
  behaviour.
- `ALLOW_HFP_PROFILE` — HFP/HSP authorisation override.
- `TRUSTED_PROXIES` — X-Forwarded-For accept list.

Two new tests in `tests/test_translate_ha_config.py` pin the
experimental-flags group and the broader web-UI-only group so a
future field added to either family doesn't silently regress on
addon-mode restarts.

### Diagnostics — surface MA server version

The bug-report payload (`/api/bugreport`) and the runtime log now
both expose the **Music Assistant server version**.  Previously only
the bridge's `music-assistant-client` *library* pin (e.g.
`1.3.5`) appeared in the report, which left auth-flow incidents
like #190 stuck on "what MA build is the reporter running?" before
any debugging could start.

- `routes/api_status._collect_environment` — new `ma_server_version`
  key, sourced from the cached value populated at WS handshake;
  falls back to `"unknown"` if the bridge hasn't connected to MA yet
  (matches the existing `bluez` / `audio_server` pattern so the key
  always appears in the markdown body).
- `services/ma_monitor` — emits one INFO line right after the
  handshake in the form
  `MA server: version=<x> schema=<y> url=<z>` so operators can grep
  for it without trawling subprocess logs.  Mirrors the
  `entrypoint.sh` banner's style (the entrypoint can't include the
  version because it runs before any Python / WS connection).

Three new tests in `tests/test_bugreport_environment.py` pin both
the present-when-known and unknown-when-pre-handshake branches plus
the existing runtime-deps key.

### UI follow-ups on rc.8 RSSI badge

- ``_getRssiBadgeRenderData`` / ``_renderRssiBadgeHtml`` gain a
  ``mode`` argument so the chip label matches the underlying
  measurement.  Connected-link RSSI from mgmt 0x0031 is BR/EDR
  delta-from-Golden-Receive-Power-Range — labelled "Δ dB".
  Scan-result RSSI from BlueZ inquiry stays absolute "dBm".  Tooltip
  spells out the unit either way.
- Stale ``_renderRssiChip`` reference scrubbed from the
  ``services/bt_rssi_mgmt`` module docstring; now points at the
  current UI helpers.
- Default RSSI refresh interval gated by ``EXPERIMENTAL_RSSI_BADGE``
  remains opt-in (no behaviour change in this section).

## [2.63.0-rc.8] - 2026-04-26

Fixes two real bugs that VM 105 manual validation surfaced in rc.7's
RSSI restoration: the ``btsocket`` library encodes the address-type
byte as a bitmask but ``GetConnectionInformation`` expects a
discriminator, and ``gosu`` drops every capability when changing UID
so the bridge never had ``CAP_NET_ADMIN`` to query the kernel mgmt
socket in the first place.

### Fixed — direct mgmt socket bypassing btsocket's broken encoder

``services/bt_rssi_mgmt.read_conn_info`` now constructs the mgmt
opcode 0x0031 packet by hand and parses the ``CommandComplete`` event
itself.  ``btsocket.btmgmt_socket.open()`` is still used for the
socket setup (it works around `Python bug #36132 <https://bugs.python.org/issue36132>`_
via a libc-bind shim) but its protocol encoder is no longer involved.

The library's ``AddressTypeField`` encoder treats the field as a
bitmask (``1 << AddressType.BREDR.value`` ⇒ wire ``0x01``) which is
correct for some scan/filter opcodes but wrong for
``GetConnectionInformation`` where the byte is a discriminator
(BR/EDR ⇒ wire ``0x00``).  rc.7 passed an int and crashed inside
the encoder with ``'int' object is not iterable``; passing the enum
would have produced a wrong wire byte and the kernel rejected with
``MGMT_STATUS_PERMISSION_DENIED``.  Bypassing the encoder is much
simpler than monkey-patching the library.

The reader now also skips unrelated events on the shared control
channel (``IndexAdded``, other clients' ``CommandComplete``) until
our matching ``CommandComplete`` or ``CommandStatus`` arrives.

### Fixed — preserve CAP_NET_ADMIN across UID drop

``entrypoint.sh`` previously used ``gosu`` to drop the bridge process
to ``UID 1000`` for PulseAudio socket access, but ``gosu`` clears the
effective capability set during the ``setresuid``.  The kernel's
mgmt-socket bind only sets ``HCI_SOCK_TRUSTED`` when the caller has
``CAP_NET_ADMIN`` *at bind time*; without it every mgmt command for
an established link returns ``MGMT_STATUS_PERMISSION_DENIED`` (status
0x14).

Switched the launch path to ``setpriv``:

- ``--reuid`` / ``--regid`` perform the UID drop
- ``--inh-caps=+net_admin`` puts ``CAP_NET_ADMIN`` in the inheritable set
- ``--ambient-caps=+net_admin`` puts it in the ambient set so it
  survives both ``execve`` and the ``setresuid``

Result: the Python process runs as UID 1000 with ``CapEff=0x1000``
(``CAP_NET_ADMIN`` only — every other cap dropped, no escalation
beyond the strict minimum).  ``gosu`` is kept as a fall-back when
``setpriv`` is unavailable; in that case RSSI degrades to the
existing fail-soft "no fresh value, keep last known".

Verified end-to-end on VM 105: bridge process ``CapEff=0000000000001000``,
mgmt socket bound trusted, ``CommandComplete`` arrives with the
expected payload, ``DeviceStatus.rssi_dbm`` populates and flows
through SSE to the UI chip.

### Tests

Unit tests rewritten around two new seams:

- ``read_conn_info`` is the public API — sentinel/sign handling.
  Mocked at ``_query_rssi_byte`` to feed it specific raw bytes.
- ``_query_rssi_byte`` is the syscall layer — mocked at
  ``_open_mgmt_socket`` with a fake socket that replays pre-baked
  ``CommandComplete`` blobs, exercising the wire-format parser and
  the event-skipping loop.

14 cases in ``tests/test_bt_rssi_mgmt.py`` (was 9 in rc.7) cover:
the wire-byte parse, opcode/index/addr-type packet shape, the
event-skipping loop, ``CommandStatus`` failures, non-zero
``CommandComplete`` status, ``ImportError`` and other open failures,
malformed MAC, plus the original sentinel/sign/short-circuit logic.

## [2.63.0-rc.7] - 2026-04-25

Restores the live RSSI display for connected speakers — the third (and
working) attempt after rc.3 (``scan bredr``) and rc.5
(``bluetoothctl info``) both proved BlueZ exposes nothing for an
established ACL link via userspace tools.

### Added — connected-device RSSI via kernel mgmt socket

The remaining viable source on Linux is the BlueZ kernel mgmt
interface: opcode 0x0031 (``MGMT_OP_GET_CONN_INFO``) returns the
controller-measured RSSI for a connected peer, BR/EDR or LE.  Wrapped
in ``services/bt_rssi_mgmt.py`` via the ``btsocket`` library so we
don't hand-roll the binary protocol.  Requires ``CAP_NET_ADMIN`` —
the bridge container already has it.

- ``services/bt_rssi_mgmt.read_conn_info(adapter_index, mac)`` — sync
  wrapper, returns signed dBm or ``None``.  Every failure mode (peer
  not connected, EPERM, ENODEV, status != Success, sentinel 127,
  btsocket missing on non-Linux test envs, garbage adapter index)
  collapses to ``None`` so callers' contract is "fresh value or keep
  last known — never propagate an exception".
- ``BluetoothManager.run_rssi_refresh_loop`` (every 30 s) +
  ``_rssi_refresh_tick`` — short-circuits when the link is down, when
  the shared ``bt_operation_lock`` is held by a pair / scan /
  reconnect, or when the adapter index can't be resolved from
  ``adapter_hci_name``.  Spawned alongside ``monitor_and_reconnect``
  in ``sendspin_client._run_async``.
- ``BluetoothManager.__init__`` gains an ``on_rssi_update`` callback;
  ``services/device_activation.activate_device`` wires it to
  ``SendspinClient._update_status({"rssi_dbm": …, "rssi_at_ts": …})``
  so values flow through the existing SSE pipeline into the UI chip
  (``_renderRssiChip``) that's been there since rc.2.

Tests: ``tests/test_bt_rssi_mgmt.py`` (8 cases pinning every
short-circuit and the unsigned→signed byte fold);
``tests/test_bt_rssi_refresh.py`` (9 cases covering adapter-index
resolution and every refresh-tick branch including lock contention
and callback exceptions).

### Changed

- ``btsocket==0.3.0`` added to ``requirements.txt``.  Linux-only
  transitively; ``services.bt_rssi_mgmt`` catches ``ImportError`` so
  developer macOS test runs stay green without it.
- ``bluetooth_manager.py`` rc.6 docblock replaced; ``asyncio`` lifted
  out of the ``TYPE_CHECKING`` block (now used at runtime by
  ``run_rssi_refresh_loop``).

## [2.63.0-rc.6] - 2026-04-25

Two findings from VM 105 manual validation of rc.5 forced an
architecture pivot on MPRIS and a deferral on background RSSI.

### Fixed — MPRIS AVRCP forwarding actually works now

rc.1 through rc.5 ``bus.export``-ed the per-device MPRIS Player
object and tried to claim a well-known
``org.mpris.MediaPlayer2.sendspin_*`` bus name on the SYSTEM bus.
**Neither step is what BlueZ uses for AVRCP forwarding** — the
system-bus name request was silently rejected by default ACL, and
BlueZ doesn't scan bus names for MPRIS players; it only routes to
paths handed to it via ``org.bluez.Media1.RegisterPlayer``.  Result:
physical Play / Pause / Next / Previous on connected speakers had no
effect across all of rc.1-rc.5.

Fix in ``services/device_activation.py``:

- Drop the well-known name request entirely (was failing silently
  and not needed).
- Per-device path moved to a unique
  ``/org/sendspin/players/<MAC>`` so multiple speakers on the same
  adapter can each register without clashing.
- After ``bus.export(...)``, call
  ``org.bluez.Media1.RegisterPlayer(path, properties)`` on the
  device's adapter (``/org/bluez/<hciN>``).  The properties dict
  carries the AVRCP advertisement (PlaybackStatus, CanPlay, etc).
  BlueZ then routes inbound AVRCP passthrough commands to the
  exported MPRIS Player methods.
- Symmetric ``Media1.UnregisterPlayer`` on the disconnect hook
  before un-exporting, so BlueZ doesn't keep a dangling pointer.
- ``services/mpris_player.py`` ``_on_play`` / ``_on_pause`` /
  ``_on_play_pause`` / ``_on_stop`` / ``_on_next`` / ``_on_previous``
  now log INFO ``MprisPlayer[<MAC>]: AVRCP <command>`` so support
  has a single grep to confirm the round-trip lands.

Verified end-to-end on VM 105: pressing Pause on the ENEBY
Portable's physical button produces an
``MprisPlayer[6C:5C:...]: AVRCP Pause`` log line and the bridge
forwards the transport command to MA.

Tests: ``test_mpris_object_path_is_per_device_unique_for_bluez_register_player``
and ``test_bluez_adapter_path_returns_org_bluez_hci_form`` lock the
new contract.  ``_mpris_well_known_name`` removed (test for it
deleted).

### Removed — periodic RSSI background refresh

rc.3 added it via ``scan bredr`` (failed: connected BR/EDR peers
don't reappear in inquiry); rc.5 retried via ``bluetoothctl info``
(failed: BlueZ doesn't poll the RSSI line for connected peers and
it's absent from the property output); D-Bus ``Device1.RSSI`` only
populates during discovery and only for devices that actually
respond to inquiry — most connected BR/EDR peers don't.

The remaining viable source is the kernel mgmt socket
(``MGMT_OP_GET_CONN_INFO`` — ``CAP_NET_ADMIN`` plus ~150 LoC of
binary serialisation), deferred to v2.64+.

This rc removes the broken machinery so the no-op codepath stops
spamming the BT operation lock with empty bursts:

- ``BluetoothManager.run_rssi_refresh`` /
  ``run_rssi_refresh_loop`` deleted.
- ``_RSSI_LINE_RE`` / ``_RSSI_INFO_LINE_RE`` /
  ``_parse_own_rssi_from_burst`` / ``_parse_rssi_from_info`` /
  ``_RSSI_REFRESH_INTERVAL_S`` deleted.
- ``rssi_task`` spawn in ``sendspin_client._run_async`` removed.
- ``tests/test_bt_rssi_refresh.py`` deleted (parsers were only
  exercised here; scan-time RSSI in ``routes/api_bt.py`` keeps its
  own independent parser + tests).

Kept: ``DeviceStatus.rssi_dbm`` / ``rssi_at_ts`` fields, the UI
``_renderRssiChip`` helper, and the scan-time RSSI population in
``routes/api_bt.py``.  All work without WS dependencies and stay
ready for the future mgmt-socket revival to fill them in for
connected devices.

## [2.63.0-rc.5] - 2026-04-25

Two latent bugs surfaced during VM 105 manual validation of rc.4 —
both pre-existing (rc.1 and rc.3) but only observable end-to-end now.

### Fixed

- **MPRIS ``on_connected`` hook never fired** (rc.1 regression).
  ``bluetooth_manager.py`` and ``bt_monitor.py`` mutated
  ``self.connected`` / ``mgr.connected`` directly in ~10 sites
  (D-Bus PropertiesChanged path, reconnect loop, dance recovery,
  ``_connect_device_inner``), bypassing
  ``_fire_connection_transition``.  The on_connected callback —
  the path that registers the per-device MprisPlayer — silently
  never ran on the primary D-Bus connect signal, so physical
  AVRCP buttons on connected speakers had no effect in production.

  Fix: introduce ``BluetoothManager._apply_connected_state(value)``
  as the single setter that bookkeeps both the cached state and the
  transition fire.  Replaced every direct assignment with a call to
  this helper.  Regression test
  ``test_apply_connected_state_called_by_dbus_props_changed_path``
  parses ``bt_monitor.py`` source and asserts no remaining direct
  ``mgr.connected = X`` sites — kills this whole class of drift in
  future patches.

- **RSSI background refresh always returned ``None``** (rc.3
  design flaw).  ``_run_rssi_burst`` ran ``bluetoothctl scan bredr``
  for 5 s then parsed ``[CHG] Device <MAC> RSSI:`` events.  Already-
  connected BR/EDR peers stop advertising after the ACL link is
  established, so the burst window saw zero events for our MAC and
  the parser returned ``None`` every tick — connected device cards
  never showed an RSSI badge despite the loop running every 60 s.

  Fix: read RSSI from ``bluetoothctl info <MAC>`` instead, which
  exposes the live ACL link RSSI from BlueZ's connection cache.
  New parser ``_parse_rssi_from_info`` handles both bluetoothctl
  formats (decimal + parenthesised hex).  ``_run_rssi_burst``
  removed (was dead code after the switch).  The kernel mgmt-socket
  ``MGMT_OP_GET_CONN_INFO`` would be the cleanest source long-term
  but adds raw-socket complexity not justified for this rc.

### Changed

- ``bluetooth_manager._run_rssi_burst`` removed.  External callers
  inside the project: none.

### Tests

- ``tests/test_bt_manager.py`` — 3 new tests for
  ``_apply_connected_state`` (transition fire, thread-safety stress,
  AST-based ``bt_monitor`` source audit).
- ``tests/test_bt_rssi_refresh.py`` — 4 tests rewired to mock
  ``_run_bluetoothctl`` for the new info-query path; 1 new test
  asserting ``info <MAC>`` is the actual command issued.

### Fixes from initial review (PR #199)

- ``bluetooth_manager._apply_connected_state`` — guard the
  check-then-set sequence with ``self._connected_state_lock`` so
  the asyncio D-Bus monitor thread and the BT executor thread
  cannot both observe ``self.connected==False``, both pass the
  check, and both fire ``on_connected`` (would surface as duplicate
  MprisPlayer D-Bus exports).  Callback runs OUTSIDE the lock so a
  slow callback can't block a concurrent disconnect handler.
- ``bluetooth_manager.py`` — rewrite the misleading
  ``_RSSI_LINE_RE`` doc-comment that referenced
  ``routes/api_bt.py`` (which keeps its own near-identical regex
  rather than importing this one).
- ``tests/test_bt_manager.py`` — replace the substring-based
  ``bt_monitor`` source-audit with an ``ast``-based walk that
  catches ``Assign`` / ``AugAssign`` / ``AnnAssign`` nodes
  targeting ``mgr.connected``; immune to false positives in
  docstrings / log strings and to false negatives from non-standard
  whitespace.

## [2.63.0-rc.4] - 2026-04-25

Pivot rc — rolls back the rc.3 WebSocket migration; ships the
correct fix for the original HA Supervisor SSE corruption.

### Why

Manual validation of rc.3 on VM 105 surfaced
``RuntimeError: Cannot obtain socket from WSGI environment.`` on
every WS connect.  ``flask-sock`` / ``simple-websocket`` need
raw-socket access from the WSGI env; ``waitress`` doesn't expose
it.  Alternatives (gunicorn+gevent monkey-patch, embedded
``gevent.pywsgi``, ASGI migration) all carry asyncio-orchestrator
risk or scope beyond v2.63.0.

### Fixed

- ``routes/api_status.py`` — set ``Cache-Control: no-transform``
  (RFC 7234 §5.2.2.4) + ``Content-Encoding: identity`` on the SSE
  response so HA Supervisor ingress no longer applies deflate
  compression that corrupts ``text/event-stream`` payloads.

### Reverted from rc.3

- ``web_interface.py`` — drop ``Sock(app)`` + ``register_ws_routes``;
  ``flask-sock`` dropped from ``requirements.txt``.
- ``static/app.js`` — UI back to SSE-only for status, polling-only
  for logs.

### Kept

- ``services/bt_operation_lock.py`` shared lock + RSSI background
  refresh + ``_RingLogHandler.subscribe_with_snapshot`` API.  All
  work without WS and stay tested.  ``routes/api_ws.py`` generators
  kept as dormant contracts for a future ASGI revival.

## [2.63.0-rc.3] - 2026-04-25

Transport-layer rc: replaces SSE with WebSocket for the status stream
(closes the long-standing HA Supervisor deflate-compression failure
mode), adds a real-time log panel over WebSocket, and turns on the
periodic RSSI refresh deferred from rc.2 so connected device cards
finally render an up-to-date dBm badge without waiting for a manual
scan.

### Added — WebSocket status stream

- ``routes/api_ws.py:status_ws_iter`` — pure generator yielding the
  initial snapshot, then per-change snapshots, then heartbeats on idle
  ticks; matches the SSE 30-min lifetime cap.
- ``routes/api_ws.py:register_ws_routes`` registers
  ``/api/status/ws`` on a ``flask-sock`` ``Sock(app)`` instance wired
  in ``web_interface.py`` (best-effort: SSE keeps serving on dev
  hosts without the dep).
- ``static/app.js`` — UI prefers WS first, falls back to the existing
  SSE handler then 2 s polling, with capped retry / backoff.
- ``requirements.txt`` — new ``flask-sock>=0.7.0,<1.0`` (pulls
  ``simple-websocket`` / ``wsproto`` / ``h11``).

### Added — Live log stream

- ``sendspin_client._RingLogHandler.subscribe`` /
  ``unsubscribe`` /  ``snapshot`` — the in-process ring buffer now
  fans out per-emit lines to subscribed queues so the WS endpoint can
  push new log lines without polling.
- ``routes/api_ws.py:log_stream_iter`` — yields a ``snapshot`` frame
  on connect (full ring contents), then ``append`` frames per emit;
  unsubscribes via ``finally`` so closed clients can't leak fan-out
  work.
- ``routes/api_ws.py`` registers ``/api/logs/stream``.
- ``static/app.js:startLogsWebsocket`` /  ``stopLogsWebsocket`` —
  Auto-Refresh toggle now drives a WS subscription for real-time
  appends and falls back to a 5 s safety-net poll (was 2 s) only if
  WS connect fails.

### Added — Periodic RSSI refresh (rc.2 follow-up)

- ``bluetooth_manager.py:_parse_own_rssi_from_burst`` — pure parser
  for the most-recent ``[CHG] Device <MAC> RSSI: <dB>`` event in a
  ``bluetoothctl`` burst window (handles modern decimal + legacy
  parenthesised hex formats).
- ``bluetooth_manager.BluetoothManager.run_rssi_refresh`` runs a 5 s
  ``scan bredr`` burst, parses our MAC's most recent RSSI, pushes
  ``rssi_dbm`` + ``rssi_at_ts`` onto host status.  Skips when a
  user-triggered scan owns BlueZ discovery (via
  ``services.async_job_state.is_scan_running``).
- ``run_rssi_refresh_loop`` is the async wrapper, spawned alongside
  ``monitor_and_reconnect`` from ``sendspin_client._run_async`` so
  the interval ticks every 60 s for every connected speaker.

### Tests

- ``tests/test_status_ws.py`` — 11 new tests covering the
  ``status_ws_iter`` and ``log_stream_iter`` generators (initial
  snapshot, change pushes, heartbeats, max iterations / lifetime,
  subscriber cleanup, atomic subscribe-with-snapshot, bounded queue).
- ``tests/test_ring_log_handler_subscribe.py`` — 10 new tests for the
  ring buffer subscribe/unsubscribe/snapshot API + multi-subscriber
  fan-out + bad-subscriber isolation + concurrent snapshot/emit
  stress + atomicity of ``subscribe_with_snapshot``.
- ``tests/test_bt_rssi_refresh.py`` — 9 new tests for the parser and
  the ``run_rssi_refresh`` orchestration (push on hit, no-op on miss,
  skip on user scan, swallow burst failures).

### Fixes from initial review (PR #197)

- ``sendspin_client._RingLogHandler`` — single ``self._lock`` now
  guards both ``records`` and ``_subscribers`` so ``snapshot()`` no
  longer races ``emit()`` (Copilot flagged the deque-iteration
  hazard).  New ``subscribe_with_snapshot()`` exposes an atomic
  take-snapshot-then-register pair so the WS log stream cannot drop
  lines emitted in the gap between the two ops.
- ``routes/api_ws.log_stream_iter`` — uses
  ``subscribe_with_snapshot`` (no race), bounds the per-client queue
  at ``LOG_STREAM_QUEUE_MAXSIZE`` (newest line drops at the
  ``put_nowait`` step when a stalled browser tab fills the queue;
  the ring buffer covers the gap on the client's next reconnect).
- ``routes/api_ws._api_logs_stream`` — explicit ``stream.close()`` in
  ``finally`` so the generator's unsubscribe hook always runs even
  when ``ws.send`` raises on client disconnect (no leaked
  subscribers).
- ``web_interface.py`` — narrow the WS-import soft-fallback to
  ``ImportError``/``ModuleNotFoundError``; any other exception now
  surfaces via ``logger.exception`` and re-raise so real bugs in
  ``routes/api_ws`` aren't silently swallowed.
- ``static/app.js`` — single ``beforeunload`` handler (was added on
  every reconnect, leaking duplicate listeners); status WS
  ``session_expired`` now closes the current socket and lets
  ``onclose`` drive reconnect (no overlapping sockets); logs WS
  tracks its reconnect timer id and clears it in
  ``stopLogsWebsocket`` so toggling Auto-Refresh off can't reconnect
  later.

### Second-round review fixes (PR #197)

- ``routes/api_ws.py`` — both WS handlers now catch
  ``simple_websocket.ConnectionClosed`` explicitly (debug-logged) and
  route any other exception through ``logger.exception`` so real bugs
  (JSON encoding errors, payload build errors) surface in production
  instead of being silently swallowed.  ``LOG_STREAM_QUEUE_MAXSIZE``
  comment rewritten to describe the actual drop-newest-at-producer
  policy (the previous wording mistakenly described drop-oldest).
- ``web_interface.py`` — drop redundant ``ModuleNotFoundError`` from
  the WS-import except clause (it's a subclass of ``ImportError``).
- ``tests/test_status_ws.py`` — fixture ``_reset_status_version``
  now waits for ``notify_status_changed``'s 100 ms debounce timer to
  flush before yielding so the heartbeat-vs-change tests don't race
  the timer under load.  Renamed
  ``test_log_stream_iter_queue_is_bounded_drops_oldest_on_overflow``
  → ``..._drops_newest_when_full`` to match the actual implementation.

### Third-round review fixes (PR #197)

- ``routes/api_ws.py:status_ws_iter`` — capture
  ``get_status_version`` BEFORE building the initial snapshot.  The
  previous order let a status change landing between snapshot build
  and version capture silently slip through: ``last_version`` would
  already record the post-change number, so ``wait_for_status_change``
  never fired for it and clients silently saw stale state until the
  next change.  Regression test injects a notify during snapshot
  construction and asserts the next yield is a change frame.
- ``services/bt_operation_lock.py`` (new) — extracts the
  ``_bt_operation_lock`` previously private to ``routes/api_bt.py``
  into a shared module so background callers can also acquire it.
  ``BluetoothManager.run_rssi_refresh`` now acquires it non-blocking
  and skips the burst when held — its 60 s cadence makes a missed
  tick cheap, and it can no longer corrupt a parallel pair / reset
  / standalone-scan session by sharing BlueZ's discovery state.
- ``static/app.js`` — reset ``_logsWsRetries`` in both
  ``startLogsWebsocket`` and ``stopLogsWebsocket`` so a previous
  exhausted retry budget can't silently block reconnect when the
  user toggles Auto-Refresh off then back on.

## [2.63.0-rc.2] - 2026-04-25

UX polish on top of rc.1: signal-strength visibility, safer default
codec selection, and an opt-in keepalive payload for the few speakers
that misbehave on the 2 Hz infrasound burst.

### Added — RSSI / signal strength badge (plan item 3)

- ``routes/api_bt.py`` — ``_parse_scan_output`` now returns a
  ``rssi_by_mac: dict[str, int]`` alongside the existing tuple, capturing
  every ``[CHG] Device <MAC> RSSI: <dB>`` event.  Both modern decimal
  (``-43``) and legacy parenthesised hex (``0xff... (-43)``) formats
  parse to the same signed int.  ``_extract_rssi_from_info`` reads the
  matching ``RSSI:`` line out of ``bluetoothctl info <MAC>`` for already
  connected peers that don't appear in the live scan stream.
- ``sendspin_client.py:DeviceStatus`` — new ``rssi_dbm`` and
  ``rssi_at_ts`` fields.  Status snapshots include them by default; the
  values come back as ``None`` when no reading has been captured yet.
- ``static/app.js`` — new ``_renderRssiChip`` helper renders the colour
  bands (green ≥ -65, yellow -75…-65, red ≤ -75, grey when stale > 90 s)
  on both scan-result rows and per-device cards (``drssi-N`` slot).
  ``static/style.css`` — matching ``rssi-good`` / ``rssi-fair`` /
  ``rssi-bad`` / ``rssi-stale`` palette.

### Added — Block HSP/HFP profiles by default (plan item 7)

- ``services/pairing_agent.py`` — split out a new ``_HFP_SERVICE_UUIDS``
  frozenset (HSP Headset / HSP AG / HFP Hands-Free / HFP AG) and gate
  it behind a per-agent ``allow_hfp`` flag (default ``False``).  Some
  DSPs (Bose QC, AKG Y500) prefer HFP over A2DP when both are accepted,
  collapsing the link to an 8 kHz mono call codec — block by default
  to preserve A2DP stereo.
- ``config.py:DEFAULT_CONFIG`` — new ``ALLOW_HFP_PROFILE`` boolean
  (default ``False``).  Set to ``True`` to restore the pre-rc.2
  behaviour for HFP-only headphones (rare).
- ``bluetooth_manager.py``, ``routes/api_bt.py`` — every
  ``PairingAgent(...)`` construction reads the live config flag and
  threads it into the agent.

### Added — keep_alive_method enum (plan item 8 polish)

- ``sendspin_client.py:_generate_keepalive_buffer(method)`` selects the
  PCM payload for the keepalive burst:
  ``infrasound`` (default — existing 2 Hz subsonic stereo at -50 dB),
  ``silence`` (zero PCM, same length, for speakers that misbehave on
  the 2 Hz tone), or ``none`` (skip — let the speaker time out
  naturally).  Unknown values fall back to ``infrasound`` so a typo
  in per-device config can't silently disable keepalive.
- ``services/device_activation.py`` — reads ``keep_alive_method`` from
  per-device config and threads it into ``SendspinClient``.
- ``config.schema.json`` — new ``keep_alive_method`` device option
  with the three-value enum.

### Fixed

- ``services/ma_runtime_state.py:get_ma_group_for_player_id`` — when a
  bridge player is a member of multiple MA syncgroups simultaneously,
  the lookup now walks ``_ma_all_groups`` and prefers whichever
  syncgroup has an active now-playing state (``playing`` / ``paused`` /
  ``buffering``) over an idle sibling.  Falls back to the
  first-write-wins ``_ma_groups`` mapping when none are active.
  Symptom on VM 105: ENEBY Portable @ DOCKER (member of both
  "Sendspin BT" and "Sendspin RC") was permanently labelled "Sendspin
  BT" with no track metadata even while the speaker was actively
  streaming as part of the "Sendspin RC" syncgroup.

### Fixes from initial review (PR #196)

- ``static/app.js:_renderRssiChip`` — relabel chip + tooltip to
  ``dBm`` (Bluetooth RSSI is dBm by spec, was rendering as ``dB``).
- ``sendspin_client.py:DeviceStatus`` — rewrite the ``rssi_dbm`` /
  ``rssi_at_ts`` field comment to reflect that scan-path population
  is the only writer in rc.2; the periodic background refresh that
  keeps connected device cards warm is deferred to rc.3.  Behaviour
  unchanged.
- ``config.schema.json`` — declare top-level ``ALLOW_HFP_PROFILE``
  boolean so the schema documents the runtime config key added in
  this rc.  ``tests/test_config.py`` gains a regression test that
  asserts every ``DEFAULT_CONFIG`` key (other than the internal
  ``CONFIG_SCHEMA_VERSION``) is declared in ``config.schema.json``,
  catching this whole class of drift in future rcs.

### Tests

- ``tests/test_bt_scan_rssi.py`` — 8 new tests covering both
  bluetoothctl RSSI formats, the ``_extract_rssi_from_info`` helper,
  legacy active-MAC contract preservation, and ``DeviceStatus`` field
  defaults.
- ``tests/test_pairing_agent.py`` — 7 new tests for the HFP gate
  (default-rejects all four UUIDs, opt-in accepts, A2DP unaffected
  either way).
- ``tests/test_standby_daemon.py`` — 4 new tests for
  ``_generate_keepalive_buffer`` (infrasound parity, silence is zeros,
  none is empty, unknown falls back).
- ``tests/test_ma_runtime_state.py`` — 5 new tests covering the
  multi-syncgroup lookup contract (active-state preference, paused
  preferred over idle, all-idle falls back to cached mapping,
  single-group case unchanged, unknown player returns ``None``).

## [2.63.0-rc.1] - 2026-04-25

Headline feature: **MPRIS Speaker Hardware Integration**.  Per-device
MPRIS Player exports on the system D-Bus enable bidirectional bridging
between the bridge and AVRCP-capable speakers (Bose, Sony WH-1000XM,
Yandex mini, etc.):

- **Speaker buttons → bridge.**  Physical Play/Pause/Next/Previous /
  absolute-volume controls on the speaker reach the daemon subprocess
  via BlueZ's MPRIS forwarding and dispatch through the same path
  ``POST /api/transport/cmd`` uses — no MA REST round-trip in the hot
  path.  See ``services/mpris_player.py`` for the per-device
  ``MprisPlayer`` and ``services/device_activation.py`` for the wiring
  hooks attached to ``BluetoothManager.on_connected`` /
  ``on_disconnected``.

- **MA playback state → speaker display.**  Now-playing snapshots from
  the MA WebSocket monitor flow into ``MprisPlayer.set_playback_status``
  / ``set_metadata``; ``PropertiesChanged`` is emitted on the bus and
  BlueZ forwards via AVRCP so speakers with displays render the current
  track / artist / album / cover art without any polling
  (``services/ma_monitor.py:push_now_playing_to_mpris``).

- **Claim Audio button.**  New ``POST /api/bt/claim/<mac>`` endpoint and
  per-device card button push ``PlaybackStatus = "Playing"`` through
  the speaker's MprisPlayer to assert the bridge as the active MPRIS
  source on multipoint speakers paired to multiple hosts.

Volume echoes from speakers (BlueZ → AVRCP → MPRIS Volume write) are
suppressed via a one-shot ``_volume_echo_pending`` guard on
``MprisPlayer`` so outbound volume sets do not loop back to MA.

### Changed

- ``BluetoothManager`` gains ``on_connected`` / ``on_disconnected``
  transition callbacks (false→true, true→false) which fire exactly once
  per transition; existing callers without these kwargs keep working
  unchanged.

### Tests

- ``tests/test_bt_manager.py`` — 8 new tests covering connect /
  disconnect transition fires, idempotency, and exception isolation.
- ``tests/test_mpris_player.py`` — 7 new tests for ``MprisRegistry``
  (lookup, MAC-case normalisation, replace semantics) + 1 Variant
  wrapping regression + 1 concurrent register/iterate stress test.
- ``tests/test_ma_monitor_mpris_bridge.py`` — solo + syncgroup → MPRIS
  bridging, idle→Stopped state mapping, MA→xesam metadata translation.
- ``tests/test_api_bt_claim.py`` — Claim Audio endpoint contract
  (success, no-player → 404, malformed MAC → 400, MAC-case tolerance,
  dash / no-separator MAC normalisation).
- ``tests/test_device_activation.py`` — 2 new tests fixing the MPRIS
  object path to the canonical ``/org/mpris/MediaPlayer2`` and
  asserting per-device well-known bus names.

### Fixes from initial review (PR #195)

- **``services/mpris_player.py``** — wrap MPRIS ``Metadata`` values in
  ``Variant`` before ``emit_properties_changed``; previous code
  forwarded a flat dict and would have crashed dbus_fast on the
  ``a{sv}`` signature.
- **``services/mpris_player.py``** — guard ``MprisRegistry`` with a
  ``threading.Lock`` and iterate over snapshots; the singleton is
  touched by BT manager threads, Flask request threads, and the asyncio
  loop simultaneously.
- **``routes/api_bt.py``** — canonicalise MAC before validation in
  ``POST /api/bt/claim/<mac>`` so dash-separated and compact (no
  separator) forms match the registry's normalisation.
- **``services/device_activation.py``** — claim a per-device well-known
  ``org.mpris.MediaPlayer2.sendspin_<MAC>`` bus name and export at the
  canonical ``/org/mpris/MediaPlayer2`` object path; without the
  well-known name BlueZ' MPRIS bridge and other MPRIS clients can't
  discover the player.

## [2.62.0-rc.13] - 2026-04-25

Image size audit — five safe cleanups bundled together remove
~38 MB of cruft that was either left over by mistake (pip) or
unreachable at runtime (udev hwdb, systemd units, package docs).
Smoke-tested live inside a running container: all critical imports
(config, sendspin_client, services.*, routes.*, qrcode, av, PIL,
cryptography) succeed, ``/api/health`` returns ``{"ok": true}``.

### Removed (Dockerfile runtime stage)
- ``/usr/local/lib/python3.12/site-packages/pip`` and
  ``/usr/local/bin/pip*`` (6.6 MB).  The builder stage strips pip
  from ``/install``, but the runtime ``python:3.12-slim`` base ships
  its own pip in ``/usr/local`` which the ``COPY --from=builder``
  merges over without removing — leftover pip then survived into
  the final image.
- ``/usr/lib/udev/hwdb.bin`` and ``/usr/lib/udev/hwdb.d`` (22 MB).
  No ``udevd`` runs inside the container — BlueZ and PulseAudio
  consume udev events from the host via D-Bus, so the in-container
  hardware database is never queried.
- ``/usr/lib/systemd`` (5.6 MB).  s6-overlay handles PID 1 / signal
  forwarding; systemd unit files and helpers are unreachable.
- ``/usr/share/doc/*``, ``/usr/share/man/*``, ``/usr/share/info/*``
  (~4.4 MB).  Standard slim-image practice — package documentation
  pulled in by apt-installed runtime deps has no consumer.
- ``pulsectl/tests``, ``qrcode/tests``, ``numpy/doc`` inside
  ``site-packages`` (~80 KB).  Test suites and module docs from
  installed wheels.

## [2.62.0-rc.12] - 2026-04-25

UI fix: the "Update Available" modal's release-notes pane was
mangling GitHub release bodies — RST-style ``\`\`code\`\``` spans
showed literally with both pairs of backticks visible, ``### Heading``
sections collapsed to blank lines (orphaning the bullets that
followed), and the regex strip-then-textContent path offered no
inline formatting at all.

### Changed
- ``static/app.js`` — replaced the four-step regex strip in
  ``_showUpdateDialog`` (``replace(/^## .+/)``, ``replace(/^### .+/g)``
  …) with a small DOM-building markdown renderer
  ``_renderReleaseNotes(md, container)``.  Handles ``##`` and ``###``
  headings (rendered as section labels instead of erased), single and
  double backtick code spans, ``**bold**``, ``[text](url)`` links, and
  ``- `` bullets with multi-line continuation (continuation lines
  indented 2+ spaces fold into the preceding ``<li>``).  Also skips
  the auto-generated "🤖 Generated with Claude Code" footer.
- ``static/style.css`` — added classes for the new DOM:
  ``.update-modal-md-h2``, ``.update-modal-md-h3``,
  ``.update-modal-md-p``, ``.update-modal-md-list``,
  ``.update-modal-inline-code``.  Dropped ``white-space: pre-line``
  from ``.update-modal-release-copy`` (the renderer now controls
  whitespace via semantic blocks) but kept it on
  ``.update-modal-instructions-copy`` where the textContent path
  still renders.

## [2.62.0-rc.11] - 2026-04-25

Dependency hygiene pass — five Dependabot PRs (#185, #186, #187,
#188, #189) merged sequentially.  No behaviour changes; floors raised
to current upstream releases, ``cryptography`` floor bumped to address
historical CVEs in the 3.4.x line.

### Changed
- ``cryptography`` floor in ``requirements.txt``: ``>=3.4.0`` →
  ``>=46.0.7``.  Production dependency, transitively used by PyJWT
  (HA login_flow / TOTP) and ``music-assistant-client``.  The 3.4.x
  series (2021) carries multiple known CVEs (e.g. CVE-2023-50782,
  CVE-2024-26130); raising the floor forces fresh installs onto a
  patched version while keeping the upper bound open.  armv7
  compatibility verified: ``cryptography==46.0.7`` ships
  ``manylinux_2_31_armv7l`` wheels which install cleanly on the
  bridge's ``python:3.12-slim`` (Debian Bookworm, glibc 2.36) base
  used for both the standalone Docker image and the HA addon armv7
  build.
- ``pytest`` floor: ``>=8.0.0`` → ``>=9.0.3`` (dev only).
- ``pytest-asyncio`` floor: ``>=0.23.0`` → ``>=1.3.0`` (dev only;
  major-version bump through the 1.0 break, fully validated by the
  existing 1547-test suite that has been green on ``1.3.0`` locally
  for the rc cycle).
- ``mypy`` floor: ``>=1.20.1`` → ``>=1.20.2`` (dev only).
- ``ruff`` exact pin in ``pyproject.toml``: ``==0.15.10`` →
  ``==0.15.12`` (Dependabot picked the freshest patch during the
  rebase window).

## [2.62.0-rc.10] - 2026-04-25

Copilot review pass on the rc.9 PR (#194).  No behaviour changes —
performance / robustness polish on top of the rc.8 + rc.9 cleanup.

### Changed
- ``GET /api/bt/adapters`` no longer walks ``/sys/class/bluetooth``
  once per adapter.  New ``services.bluetooth.build_hci_map()`` scans
  sysfs once per request and returns a ``{normalised_mac: hciN}``
  map, dropping the endpoint from O(n²) to O(n) in the number of
  adapters.  ``resolve_hci_for_mac`` now thinly wraps the same
  helper so its single-MAC use cases (``scripts/translate_ha_config.py``)
  stay backward-compatible.
- ``services.pa_volume_controller._handle_sink_event`` now takes the
  already-open ``PulseAsync`` connection from the subscribe loop and
  reads sink state via the new ``services.pulse.aread_sink_state``
  helper.  Each PA sink event used to spawn a fresh ``PulseAsync``
  client through the one-shot ``aget_sink_volume`` /
  ``aget_sink_mute`` helpers — under frequent updates that meant
  noticeable connection churn against the PA daemon.  The subscribe
  loop's own connection now serves both the event stream and the
  per-event read.
- ``PulseVolumeController.stop_monitoring`` no longer swallows every
  exception when awaiting the cancelled monitor task.  ``CancelledError``
  is still treated as the expected outcome; any other exception is
  logged at DEBUG so a subscribe-loop crash during shutdown leaves a
  trace instead of disappearing silently.

### Fixed
- ``CHANGELOG.md`` rc.9 section dated ``2026-04-24`` while rc.8 was
  dated ``2026-04-25`` — corrected to ``2026-04-25``.

## [2.62.0-rc.9] - 2026-04-25

Continuation of the rc.8 simplification work: with sendspin's
``PulseVolumeController`` doing real two-way state sync, the bridge
no longer needs to maintain its own artwork-frame relay or its
backwards-compat shims for sendspin <5.5.0.  ``requirements.txt``
pins ``sendspin==7.0.0``, so the legacy paths can never execute.

### Removed
- **Sendspin artwork binary-frame relay**.  ``BridgeDaemon`` no
  longer monkey-patches the sendspin client's
  ``_handle_binary_message`` to intercept ``ArtworkFrame`` payloads
  (``_patch_artwork_handler``, ``_on_artwork_frame``,
  ``Roles.ARTWORK`` advertisement, ``ArtworkChannel`` /
  ``ClientHelloArtworkSupport`` imports, ``base64`` import).  The
  web UI already uses MA's ``image_url`` via the HMAC-signed
  ``/api/ma/artwork`` proxy, so this ~80 LoC of fragile
  monkey-patched code never reached the user.
- **Legacy sendspin <5.5.0 fallback** in ``BridgeDaemon``.  Removed
  ``_has_upstream_volume_controller`` / ``_sync_bt_sink_volume``
  manual ``aset_sink_volume`` path, the ``on_volume_save`` callback
  parameter, and the ``_background_tasks`` book-keeping set.  The
  pinned sendspin always provides ``PulseVolumeController.set_state``,
  so ``_handle_server_command`` only mirrors the value into
  ``bridge_status`` and notifies SSE listeners.
- **Legacy ``use_hardware_volume`` filter** in ``daemon_process``.
  ``DaemonArgs`` always accepts ``volume_controller`` on sendspin
  7.0.0; the kwarg-based fallback the bridge used to keep when
  ``volume_controller`` wasn't supported is gone.  The generic
  ``_filter_supported_daemon_args_kwargs`` helper still drops any
  unknown keys (kept tested by ``test_daemon_process.py``), so
  forward-compat with future sendspin signatures is unchanged.
- ``daemon._sync_bt_sink_volume(vol)`` call from the IPC
  ``set_volume`` handler in ``services/daemon_process.py:_read_commands``
  (the method no longer exists).  PulseVolumeController already
  drives the actual sink volume from inside sendspin.
- Removed test classes ``TestArtworkCallback``, ``TestArtworkMonkeyPatch``
  and ``TestUpstreamVolumeController`` from
  ``tests/test_bridge_daemon_features.py`` (covered code is gone).
  ``TestClientHelloRoles`` renamed to
  ``test_create_client_advertises_only_supported_roles`` and now
  asserts that neither ``visualizer_support`` nor
  ``artwork_support`` kwargs are passed.

### Changed
- ``BridgeDaemon._handle_server_command`` is now a thin status
  mirror: it forwards the call to ``super()._handle_server_command``
  (so sendspin's ``PulseVolumeController`` is invoked) and then
  copies ``volume`` / ``muted`` into ``bridge_status``.  No more
  branching on whether an upstream controller exists.

## [2.62.0-rc.8] - 2026-04-25

Architectural cleanup discussed on the rc.7 thread: the bridge no
longer keeps two parallel paths to push volume / mute changes to MA.
Sendspin's ``PulseVolumeController.start_monitoring`` (made real in
the previous commit) is now the single source of truth — bridge UI
volume control goes straight to pactl, and the controller's PA
event subscription pushes externally-applied changes to MA without
the bridge needing an HTTP proxy.

### Removed
- ``Route volume through MA`` and ``Route mute through MA`` toggles in
  the General settings card.  Their underlying config keys
  ``VOLUME_VIA_MA`` and ``MUTE_VIA_MA`` are dropped from
  ``config.py`` defaults, removed from the Supervisor options sync
  (``ha-addon*/config.yaml`` no longer carry ``volume_via_ma``), and
  added to the diff-config IGNORED set so old ``config.json`` files
  carrying them don't trigger spurious reconfig actions on save.
- ``routes.api._set_volume_via_ma`` / ``_set_mute_via_ma`` proxy
  helpers and the ``force_local`` request flag (no longer needed —
  the local pactl path is always taken).
- ``routes.api_config.get_volume_via_ma`` / ``get_mute_via_ma`` and
  the cached module-level ``_volume_via_ma`` / ``_mute_via_ma``
  globals.
- ``services.config_diff._GLOBAL_BROADCAST_FIELDS`` no longer lists
  the two keys.
- ``static/app.js`` ``_isMaConfigured`` / ``_refreshMaDependentToggles``
  helpers and the ``data-ma-dependent`` row machinery added in
  rc.6 — the toggles they greyed out are gone.
- ``.config-setting-row--inactive`` CSS rule (sole user gone).
- ``tests/test_volume_routing.py`` (147 LoC dedicated to the removed
  proxy paths).

### Changed
- ``POST /api/volume`` and ``POST /api/mute`` always take the direct
  pactl path now.  Sendspin's ``PulseVolumeController`` subscribes
  to PA sink change events and pushes any external state change to
  MA via the volume_controller callback, so MA's UI stays in sync
  without the bridge round-tripping through ``players/cmd/volume_set``.
- ``sendspin_client._sync_unmute_to_ma`` no longer reads
  ``get_mute_via_ma`` (toggle removed); only checks ``is_ma_connected``
  before pushing.  Kept as belt-and-suspenders for the post-spawn
  initial sync because sendspin's controller-callback path takes a
  short window to settle on first connection.
- ``scripts/translate_ha_config.py`` no longer reads
  ``volume_via_ma`` from Supervisor options.

### Tests
1568 → 1567 passing.  Removed ``tests/test_volume_routing.py``;
adjusted ``tests/test_sendspin_client_runtime.py`` (5 places) and
``tests/test_api_endpoints.py`` (~12 fixture cleanups) to drop the
removed mock targets and dead config keys.
``tests/test_config_diff.py`` test renamed and rewritten to assert
the legacy keys are now silently ignored on diff (was
"GLOBAL_BROADCAST", now "no actions") so old config.json files
don't trigger spurious reconfig on save.
``tests/test_config.py::test_load_volume_via_ma`` removed.
``tests/test_translate_ha_config.py`` cleaned of ``volume_via_ma``
references.

## [2.62.0-rc.7] - 2026-04-25

### Fixed
- **MA shows bridge players as muted even though audio is playing**
  (user-report on the HA community thread).  The daemon mutes its
  PulseAudio sink during startup to hide format-probe and routing
  glitches (``services/daemon_process.py:685``).  MA's first
  ``volume_controller.get_state()`` poll happens during that ~15-second
  window, reads ``(100, True)``, and records ``player.volume_muted=True``
  in its state.  When the startup-unmute watcher later releases the
  PA sink mute, the bridge's local ``status["muted"]`` flag is — and
  always was — ``False``, so the existing post-spawn unmute sync
  short-circuited at "already in sync" and never pushed the unmute
  back to MA.  Result: HA's MA UI kept the volume slider greyed out
  and the player labelled muted forever (until the user manually
  clicked Unmute), while audio continued playing normally.

  Fix: ``_sync_unmute_to_ma`` now accepts ``force=True``.  The
  post-spawn caller in ``_read_subprocess_output`` passes it because
  it knows the local ``status["muted"]`` doesn't reflect MA's view
  at that point (MA polled while we were startup-muted; we never
  intended to be muted ourselves).  The non-force code path keeps
  the original safety guard against double-unmuting after explicit
  user mute (#155).

### Tests
1567 → 1568 passing.  ``test_sink_unmute_skipped_when_already_in_sync``
renamed and rewritten to ``test_sink_unmute_force_pushes_to_ma_even_when_local_status_says_unmuted``
covering the regression directly, plus a new
``test_sync_unmute_to_ma_without_force_skips_when_already_unmuted``
that pins the original ``force=False`` early-exit behaviour so the
#155 protection isn't lost.

## [2.62.0-rc.6] - 2026-04-24

### Fixed
- **HA addon: ``Disable PA rescue-streams`` toggle silently reset on
  every restart** (user report).  ``routes.api_config._sync_ha_options``
  POSTs ``disable_pa_rescue_streams`` to Supervisor on every config
  save, but the option was missing from all three
  ``ha-addon*/config.yaml`` schemas.  Supervisor strips unknown options,
  so on the next addon restart ``scripts/translate_ha_config.py`` read
  the missing key as ``False`` and the bridge re-enabled
  ``module-rescue-streams``.  Added the option (default ``false``) and
  its schema type (``bool?``) to ``ha-addon/``, ``ha-addon-rc/``, and
  ``ha-addon-beta/`` ``config.yaml``.

### Tests
1542 → 1551 passing.  New ``tests/test_ha_addon_schema_sync.py``
parametrised across all three addon configs:
- every key ``_sync_ha_options`` POSTs is present in both ``options:``
  defaults and the ``schema:`` block, so the same kind of silent-reset
  regression can't slip in again,
- intentionally-unmapped keys (``auth_enabled`` — HA mode hardcodes
  auth on, no round-trip) stay out of the schema, with the exemption
  list documented in-test for review.

## [2.62.0-rc.5] - 2026-04-24

Bugfix RC for #193 — adapter listing in the web UI surfaced the wrong
``Alias:`` per MAC and the ``hciN`` label tracked BlueZ's internal
registration order instead of the kernel.

### Fixed
- **Adapter alias swap on multi-adapter hosts** (#193) — the
  ``GET /api/bt/adapters`` endpoint pieced together ``bluetoothctl
  select <MAC>; show`` per adapter and grabbed the **first** ``Alias:``
  line it found in the combined stdout.  In piped-stdin mode
  ``bluetoothctl`` interleaves the **default** controller's info ahead
  of the freshly-selected block, so the parser surfaced the wrong
  controller's alias for every non-default adapter.  Two-adapter
  systems (e.g. Pi built-in BT + USB BT500 stick) saw the alias of
  one adapter shown next to the MAC of the other.

  Replaced with the explicit ``show <MAC>`` form via the new
  ``services.bluetooth.get_adapter_alias`` helper — one targeted
  bluetoothctl invocation per MAC, no ``select``, no default-vs-
  selected ambiguity.

- **``hciN`` labels track BlueZ list order, not the kernel** (#193) —
  ``api_bt_adapters`` previously labelled adapters as
  ``f"hci{enumerate-index}"`` against the order returned by
  ``bluetoothctl list``.  That order is BlueZ's registration order
  and disagrees with the kernel ``hciN`` numbering when adapters
  hot-plug (very visible after attaching a USB stick to a Pi that
  has built-in BT).

  New ``services.bluetooth.resolve_hci_for_mac`` reads
  ``/sys/class/bluetooth/hciN/address`` (the canonical kernel mapping
  BlueZ honours) and returns the real ``hciN`` per MAC.  Endpoint
  uses it; falls back to the synthetic index label only when sysfs
  isn't mounted (non-Linux dev box, container without ``/sys``).

### Refactor
- ``scripts/translate_ha_config.py:_mac_to_hci`` is now a thin wrapper
  around ``services.bluetooth.resolve_hci_for_mac`` so HA-addon config
  translation and the live adapter endpoint share the same sysfs
  walker (DRY).

### Tests
1531 → 1542 passing.  New coverage in ``tests/test_bluetooth_svc.py``
(sysfs lookup + ``show <MAC>`` parsing, including the noisy-stdout
case that surfaced the wrong adapter's alias) plus a new
``tests/test_api_bt_adapters.py`` end-to-end module asserting:
- the endpoint labels adapters by their kernel ``hciN`` even when
  ``bluetoothctl list`` returns the USB stick first,
- each adapter's alias is the alias of its actual MAC (not the
  default controller's), and
- the bluetoothctl input is the explicit ``show <MAC>\n`` form —
  never ``select <MAC>; show``.

## [2.62.0-rc.4] - 2026-04-24

Third pass of Copilot review feedback — three concurrency / cleanup
correctness fixes.

### Fixed
- **Race in ``PairingAgent.__enter__`` error path** — when
  ``_register()`` raised, ``__enter__`` called ``_force_stop()`` which
  ran ``loop.stop()`` while the agent thread was already inside its
  ``finally`` running ``loop.run_until_complete(_unregister())``.  The
  stop interrupted the cleanup with "Event loop stopped before Future
  completed", leaking the SystemBus connection and the exported agent
  object.  Now ``_force_stop`` only calls ``loop.stop()`` while the
  thread is in ``run_forever()`` (tracked via a new
  ``_running_forever`` flag), and the ``__enter__`` error path waits
  on ``self._thread.join()`` to let cleanup finish naturally instead
  of forcing a stop.
- **Read-modify-write race on the active-clients registry** —
  ``_apply_start_client`` snapshotted ``state.get_clients_snapshot()``
  then ``state.set_clients([*snapshot, new])``.  Two parallel
  ``POST /api/config`` request threads (Waitress runs ``WEB_THREADS=8``
  by default) could each diff a different new device and clobber
  each other's append.  Replaced with a new
  ``services.device_registry.mutate_active_clients(fn)`` that takes
  the registry lock and runs the mutator atomically.  ``rollback``
  paths use the same primitive — atomic remove-by-identity instead
  of overwriting with a stale snapshot.
- **Cross-request duplicate guard** — even with the atomic mutate,
  two parallel saves could both target the same MAC.  The mutator
  now also checks for an existing client with the same MAC inside
  the lock and drops the just-built duplicate so the registry
  never holds two clients fighting for one adapter.  The dropped
  attempt skips ``client.run()`` scheduling so the daemon-spawn
  race is avoided too.

### Tests
1530 → 1531 passing.  New coverage:
- ``tests/test_reconfig_orchestrator_start_client.py`` — concurrent
  peer-request append wins; our duplicate is dropped inside the
  atomic mutate and ``client.run()`` is not scheduled.
- Existing tests refactored onto a shared ``_patch_registry``
  helper so the live-list assertions exercise the new
  ``mutate_active_clients`` path.

## [2.62.0-rc.3] - 2026-04-24

Two more follow-ups from Copilot's second pass on the PR.

### Fixed
- **Default-player-name inconsistency between startup and online
  activation** — when a ``BLUETOOTH_DEVICES`` entry omitted
  ``player_name``, the startup path defaulted to
  ``Sendspin-<hostname>`` (or ``$SENDSPIN_NAME`` / caller-override),
  but online activation hardcoded ``"Sendspin"``.  The client would
  rename itself on the next bridge restart, breaking the MA/UI
  identity mapping for that device.  ``DeviceActivationContext`` now
  carries ``default_player_name`` captured at startup, and
  ``_apply_start_client`` uses it — so live-add and restart produce
  the same name.
- **``PairingAgent.RequestPasskey`` ignored the configured PIN** —
  method used to return a hardcoded ``0`` and never mark
  ``pin_attempted``.  The legacy bluetoothctl path already handled
  both "enter pin code" and "enter passkey" prompts by writing the
  configured PIN, so devices that drove ``RequestPasskey`` instead
  of ``RequestPinCode`` failed under the native agent where the
  legacy path succeeded.  Agent now parses ``self.pin`` as an int,
  validates the 0–999999 range BlueZ requires, and returns it (or
  falls back to ``0`` with a warning if the PIN is non-numeric /
  out of range).  ``pin_attempted`` is marked either way so
  ``_run_standalone_pair_inner``'s popular-PIN retry loop still
  kicks in.

### Tests
1527 → 1530 passing.  New coverage:
- ``tests/test_pairing_agent.py`` — ``RequestPasskey`` marks the
  attempt and still falls back cleanly on an invalid (non-numeric)
  PIN.
- ``tests/test_reconfig_orchestrator_start_client.py`` —
  ``_apply_start_client`` falls through to
  ``context.default_player_name`` (not a hardcoded ``"Sendspin"``)
  when the device payload has no ``player_name``.

## [2.62.0-rc.2] - 2026-04-24

Follow-up fixes from Copilot's review of the 2.62.0-rc.1 PR.  No new
features — six bug / correctness fixes plus targeted tests.

### Fixed
- **Multi-device START_CLIENT data loss** — when the config diff
  produced several ``START_CLIENT`` actions in a single ``POST /api/config``
  (user adds three speakers at once), each call read ``existing_clients``
  from the snapshot captured at the top of ``apply()`` and wrote
  ``set_clients([*snapshot, new])`` — silently overwriting any clients
  appended by earlier iterations. Orchestrator now re-reads the live
  registry on every iteration and keeps ``clients_by_mac`` in sync,
  so all added devices land.
- **Start-client ``base_listen_port + index`` mismatch** — the fallback
  used ``len(existing_clients)`` as the device index, which could
  differ from the device's position in ``BLUETOOTH_DEVICES`` when
  disabled devices sit in front of the new one. Fixed by passing
  ``device_index`` through the action payload from ``config_diff``
  and preferring it over the live-registry length. Avoids port
  collisions on setups with disabled devices.
- **PairingAgent cleanup on registration failure** — ``_thread_main``
  exited early when ``_register()`` raised (e.g. ``AgentManager1.RegisterAgent``
  refused because another agent held the default), leaking the
  SystemBus connection and asyncio loop per failed attempt. Now
  always runs through a ``try/finally`` that closes the loop and
  best-effort unregisters if ``_bus`` was set.
- **Agent leak when ``bluetoothctl`` subprocess fails to launch** —
  in both ``_run_standalone_pair_inner`` and ``_run_reset_reconnect``
  the native-agent cleanup lived inside a ``finally`` that only ran
  after ``subprocess.Popen`` succeeded. ``PairingAgent.__exit__`` is
  now in an outer ``finally`` that always runs, guaranteeing the
  agent thread / SystemBus socket is torn down even when bluetoothctl
  can't start.
- **Stale activation context at shutdown** — ``publish_shutdown_complete``
  now calls ``set_activation_context(None)`` alongside the existing
  ``set_main_loop(None)`` so Flask threads that outlive the bridge
  process in tests / graceful shutdown can't materialize new clients
  against torn-down factories.
- **Empty assertion in ``test_activate_device_honours_effective_bridge_suffix``** —
  the test's comment promised "verify the suffix was applied" but
  didn't actually assert anything. Added the explicit
  ``captured["device_name"] == "Kitchen @ Home"`` check so the suffix
  wiring is properly regression-tested.

### Tests
1524 → 1527 passing. New coverage:
- ``tests/test_reconfig_orchestrator_start_client.py`` — three-device
  ``apply()`` call appends all three (regression for multi-device
  data-loss) and ``device_index`` from payload overrides the live
  registry length.
- ``tests/test_config_diff.py`` — ``device_index`` is attached to
  START_CLIENT payload; reflects position in ``BLUETOOTH_DEVICES``
  even when disabled devices precede the added one.

## [2.62.0-rc.1] - 2026-04-24

Follow-ups to the Synergy 65 S pair failure tracked in issue #168,
a UX polish for the device-list editor, and the big one for multi-room
ops: **adding a new speaker from the scan modal no longer forces a
bridge restart** — new devices now start live.

### Added
- **Native BlueZ authentication agent** (`services/pairing_agent.py`) —
  a ``PairingAgent`` context manager that exports ``org.bluez.Agent1``
  on the system bus via ``dbus-fast``. All 8 Agent1 methods are
  implemented; ``RequestConfirmation`` auto-confirms SSP Numeric
  Comparison passkeys directly from the BlueZ callback, eliminating
  the bluetoothctl-stdout parse/answer race that lost to BlueZ's
  internal agent timeout on slow-advertising speakers.
- **DisplayYesNo default capability** — matches what manual
  ``bluetoothctl`` advertises (the path that reached ``Bonded: yes``
  in the #168 reproduction). ``EXPERIMENTAL_PAIR_JUST_WORKS`` still
  forces ``NoInputNoOutput`` for Just-Works callers.
- **Native agent now wired into every pair path** — the scope-guard
  that left ``bluetooth_manager.pair_device`` (monitor-loop re-pair
  after bond loss) and ``routes/api_bt._run_reset_reconnect``
  (Reset & Reconnect button) on the legacy stdin-``yes`` agent is
  gone. All three pair sites now construct a ``PairingAgent`` with
  ``DisplayYesNo`` capability before spawning ``bluetoothctl``, fall
  back to the legacy agent on hosts where ``dbus-fast`` / SystemBus
  isn't reachable, and expose the same telemetry shape.
- **Pair-agent telemetry** — ``PairingAgent.telemetry`` property
  returns a stable-keys snapshot (capability, ordered method-call
  list, last passkey shown, authorized/rejected service UUIDs,
  peer-cancel flag). Each pair site logs a structured one-liner with
  this data and attaches it to the scan-job result payload so future
  support-triage can answer "which IO capability won on this
  device?" without a DEBUG log. Foundation for the roadmap's full
  pair-trace timeline (see ``ROADMAP.bluez-agent.md`` #10).
- **AuthorizeService scope** — the agent's ``AuthorizeService``
  callback used to auto-authorize any UUID the peer asked for.
  Now it accepts only audio profiles (A2DP Source/Sink, AVRCP
  Controller/Target, HSP, HFP, their AG counterparts) plus
  universally-advertised accessory services (GAP, GATT, Device
  Information, Battery) and raises ``org.bluez.Error.Rejected`` on
  everything else. Rejected UUIDs are logged and surfaced via the
  telemetry channel. Expands device support for multi-profile peers
  (some DSPs preferred HFP over A2DP when both were blanket-
  authorized); adds a small security scope-guard against unexpected
  service binds.
- **Online activation of newly-added devices** — saving a config with
  a just-added ``BLUETOOTH_DEVICES`` entry now wires up the
  ``SendspinClient`` + ``BluetoothManager`` pair, registers it in the
  device registry, and schedules ``client.run()`` on the main loop
  without a bridge restart. ``ReconfigSummary.started`` is surfaced in
  the UI as a green "Live added: <name>" toast; ``restart_required``
  stays empty for the add-device case.
- **Live re-enable of disabled devices** — toggling a device's
  ``enabled`` flag back to ``true`` at runtime now reclaims BT
  management on the existing client instead of trying to construct a
  duplicate one. ``config_diff`` emits ``START_CLIENT`` for the
  ``false → true`` transition; ``_apply_start_client`` detects the
  existing released client by MAC and calls
  ``set_bt_management_enabled(True)`` — same path the
  ``/api/bt/management`` route uses — instead of running the factory.
  Without this the UI's re-enable toggle silently no-op'd after the
  online-activation patch.
- **`services/device_activation.py`** — reusable factory
  (``DeviceActivationContext`` + ``activate_device``) shared between
  ``bridge_orchestrator.initialize_devices`` and the new
  ``ReconfigOrchestrator._apply_start_client`` path, so both entry
  points apply the same port math, keepalive clamps, sink-monitor
  wiring, and volume restore semantics.
- **`services/bridge_runtime_state.set_activation_context` /
  `get_activation_context`** — cross-thread handoff so Flask request
  threads can reach the startup-captured factories.

### Changed
- **`_run_standalone_pair_inner` uses the native agent by default** —
  when the D-Bus agent registers successfully, ``agent on`` /
  ``default-agent`` are no longer sent to bluetoothctl (avoids two
  competing agents). If ``dbus-fast`` is missing or ``RegisterAgent``
  fails, the pair flow logs a warning and falls back to the legacy
  bluetoothctl stdin-agent path unchanged — the patch is safe on
  hosts without a reachable SystemBus.
- **`ReconfigOrchestrator.__init__` accepts an optional
  `activation_context`** — needed to materialize new clients online.
  Old callers (unit tests, dev scripts) can keep passing just
  ``(loop, snapshot)``; START_CLIENT actions then fall back to the
  legacy ``restart_required`` behaviour.
- **`bridge_orchestrator.initialize_devices` now delegates per-device
  wiring to `services.device_activation.activate_device`** and
  publishes the factory context via `set_activation_context` so the
  reconfig path can reuse it. Behaviour-preserving refactor — all
  existing `test_bridge_orchestrator` assertions hold.

### Fixed
- **Scan-add no longer creates silent duplicate device rows** — the
  backend validator correctly rejected ``POST /api/config`` when two
  ``BLUETOOTH_DEVICES`` entries shared a MAC, but the UI only showed a
  single toast with no visual cue to which rows collided. Now:
  - ``addFromScan`` / ``addFromPaired`` short-circuit when a row for
    the MAC already exists: the scan modal closes, the existing row
    is highlighted, and a warning toast reports
    ``Already in device list: <name>``.
  - On a ``Duplicate MAC address: ...`` validation error at save time
    the client parses the ``errors[]`` payload and applies a red
    ``duplicate-conflict`` pulse to **every** matching row (not just
    the first), then scrolls the first offender into view.

### Scope guard
- Online activation covers the **add-device** case only. Re-enabling a
  previously-disabled device, changing the adapter on an existing one,
  and all other edits that already had hot/warm paths keep the paths
  they had before.
- Further BlueZ-agent work (UI passkey modal, per-device capability
  override, full D-Bus pair pipeline replacing ``bluetoothctl``, LE
  Audio) is tracked in ``ROADMAP.bluez-agent.md`` for 2.63+.

### Tests
1494 → 1524 passing. New coverage:

- ``tests/test_pairing_agent.py`` — capability validation, PIN plumbing,
  ``RequestConfirmation`` / ``Cancel`` state capture, full register →
  request_default_agent → unregister lifecycle against a mocked
  ``MessageBus``, and ``__enter__`` error propagation when SystemBus
  connect fails.
- ``tests/test_config_validation.py`` — additional case pinning the
  per-index ``BLUETOOTH_DEVICES[N].mac`` field path for 3+ duplicate
  MACs so the UI's conflict-row highlighter keeps parsing backend
  errors correctly as the validator evolves.
- ``tests/test_device_activation.py`` (10 cases) — factory covers
  BT-manager wiring, sink-monitor callback, missing-MAC/disabled-
  adapter degradation, volume restore, explicit vs fallback listen
  port, effective-bridge suffix, released-state restore, keepalive
  clamping, and context immutability.
- ``tests/test_reconfig_orchestrator_start_client.py`` (8 cases) —
  registry append + run-task schedule, fallback to ``restart_required``
  when context or loop absent, factory-exception error surface, MAC
  idempotency guard for already-active clients, **live re-enable of a
  released client and error surfacing when the reclaim call fails**,
  and registry rollback when the scheduled ``run()`` exits with an
  exception.

## [2.61.0-rc.7] - 2026-04-22

### Added
- **UI toggle for `EXPERIMENTAL_ADAPTER_AUTO_RECOVERY`** — the flag
  added in rc.5 was only settable by hand-editing `config.json`. The
  Settings tab now exposes it as a standard experimental row (gated
  behind the "Show experimental features" master switch) with the full
  recovery-ladder description in its tooltip.

### Changed
- **Red visual treatment for experimental toggles** — rows marked
  `data-experimental` (both `.config-setting-row` in Settings and
  `.bt-scan-toggle` in the scan modal) now render with a red tinted
  background, red inset border, and an "EXPERIMENTAL" badge in the
  top-right corner. Mirrors the amber dirty-row pattern but uses red
  so unsupported/volatile toggles are distinguishable from merely
  unsaved settings; the text badge keeps the signal legible for
  colour-blind and high-contrast users.

## [2.61.0-rc.6] - 2026-04-22

### Changed
- **Explicit A2DP Sink profile request right after pair succeeds** —
  `pair_device` now issues an explicit
  `org.bluez.Device1.ConnectProfile(A2DP_SINK_UUID)` via D-Bus
  immediately after bluetoothctl reports `Pairing successful`, before
  returning to the connect loop. On BlueZ 5.86 the generic `Connect()`
  that follows can auto-negotiate the wrong profile under the dual-role
  regression (bluez/bluez#1922), leaving the device bonded but with no
  A2DP sink published. Calling ConnectProfile while the device is still
  fresh from pair narrows that window — on a healthy stack an
  `org.bluez.Error.AlreadyConnected` response from the underlying D-Bus
  call is treated as benign, so the helper is effectively a cheap no-op.
  Best-effort: if the D-Bus call fails, the pair result is still
  reported as success and `_connect_device_inner` will retry the same
  hint after its own `Connect()`.

## [2.61.0-rc.5] - 2026-04-22

### Added
- **Experimental adapter auto-recovery ladder (opt-in)** — new
  `EXPERIMENTAL_ADAPTER_AUTO_RECOVERY` flag (default off). When the
  reconnect loop hits `BT_MAX_RECONNECT_FAILS` consecutive failures
  and the flag is on, the bridge now runs the
  [`bluetooth-auto-recovery`](https://github.com/bluetooth-devices/bluetooth-auto-recovery)
  ladder (mgmt reset → rfkill unblock → USB unbind/rebind) on the
  adapter as a last-ditch before auto-releasing BT management. If
  recovery succeeds, management stays enabled and the reconnect loop
  continues. A per-adapter 60 s cooldown prevents thrashing when
  multiple devices on the same controller hit the threshold together.
  Requires `CAP_NET_ADMIN`, `/dev/rfkill`, and `/sys/bus/usb` access
  (Docker privileged or matching capabilities) — the USB step briefly
  disconnects every device on that controller, hence opt-in.

## [2.61.0-rc.4] - 2026-04-22

### Added
- **Popular-PIN retry for legacy BT pairing** — when a BT 2.x device asks
  for a numeric PIN and rejects the bridge's default `0000` with
  `AuthenticationFailed`, the standalone pair flow
  (`POST /api/bt/pair_new`) now re-runs with the next popular PIN
  (`0000, 1234, 1111, 8888, 1212, 9999`) before giving up. Non-PIN
  failures (connection errors, timeouts) still stop the loop
  immediately — retrying against an unreachable device wasted ~20s per
  attempt. The list is intentionally short: each extra attempt adds a
  BlueZ auth-fail timeout to total pair time.

### Changed
- **Clearer pairing-failure logs** — both the scan-modal pair flow and
  the long-running reconnect pair flow now annotate the failure log
  with the rejected PIN when the device auto-prompted for one and
  `AuthenticationFailed` was seen (`… — device rejected PIN 0000`). A
  new `describe_pair_failure()` helper centralises the rule so
  operators see the root cause without grepping for
  `AuthenticationFailed`. Non-auth failures are logged verbatim as
  before.
- **Scan narrowed to BR/EDR during pairing** — `bluetoothctl scan on`
  replaced with `scan bredr` at all five pair/scan sites (reset &
  reconnect, standalone pair, background BT scan, runtime pair-device
  loop). Excluding LE-only advertisers keeps the scan window
  responsive on adapters shared with BLE traffic and avoids
  interleaved BR/EDR discovery delays seen on BlueZ 5.85
  (bluez/bluez#826). Safe on bluetoothctl ≥ 5.65.

### Fixed
- **Stale BlueZ device cache cleared on remove** — after
  `bluetoothctl remove`, `bt_remove_device` now also deletes
  `/var/lib/bluetooth/<adapter>/cache/<device>` when an adapter MAC is
  known. BlueZ leaves stale `ServiceRecords` / `Endpoints` entries in
  that file, which on re-pair surface as
  `org.bluez.Error.Failed — Protocol not available` on A2DP sinks
  (bluez/bluez#191, #348, #698). Silent if the file is absent; cleanup
  only runs when the adapter is known to avoid walking the BlueZ tree
  blindly.

## [2.61.0-rc.3] - 2026-04-22

UI follow-up to the `2.61.0-rc.1` experimental flags. No Bluetooth
pairing behaviour changes.

### Added
- **Scan-modal toggle for the NoInputNoOutput pair agent** — the
  `EXPERIMENTAL_PAIR_JUST_WORKS` config flag shipped in rc.1 with full
  config/schema/diff support, but the UI had no control for it, so
  users had to hand-edit `config.json` or `options.json` to try
  Just-Works SSP pairing. A new "NoInputNoOutput pair agent
  (experimental)" switch now appears in the scan-modal toolbar next to
  "Pause other speakers on same adapter", guarded by "Show experimental
  features". Because registering the BlueZ agent is a per-pair runtime
  decision (not a persisted setting), it lives with scan/pair context
  rather than under Settings and takes effect on the next pair attempt
  only. The toggle is only included in the `pair_new` POST body when
  the user explicitly ticks it — an unchecked toggle falls through to
  the persisted `EXPERIMENTAL_PAIR_JUST_WORKS` config key, which
  remains a usable fallback for hand-edited `config.json` /
  `options.json`.
- **`no_input_no_output_agent` per-request override in
  `POST /api/bt/pair_new`** — the scan-modal toggle sends this field on
  the pair request; when present, it wins over the persisted
  `EXPERIMENTAL_PAIR_JUST_WORKS` config key. The server accepts only
  JSON booleans here — non-bool payloads (e.g. the string `"false"`)
  are ignored rather than being coerced via `bool()`, so they fall
  through to the config key instead of silently forcing
  NoInputNoOutput.

### Tests
- `tests/test_ui_experimental_toggles.py` — regression coverage for the
  Settings-page experimental toggles (A2DP sink-recovery dance, PA
  module reload) **and** the scan-modal NoInputNoOutput pair-agent
  toggle: asserts template checkboxes exist under the right
  `data-experimental` container, asserts the Settings toggles are
  wired into `buildConfig` and populate-on-load, and asserts the
  scan-modal toggle is passed as `no_input_no_output_agent` in the
  `pair_new` request body only when the checkbox is ticked (i.e. not
  baked into the body literal unconditionally) and is never persisted
  via `buildConfig`. Would have caught the rc.1 omission immediately.
- `tests/test_api_endpoints.py` — five new tests covering the
  per-request override precedence (override beats config both ways),
  endpoint forwarding of the new body field, `None`-fallback when the
  field is omitted, and strict bool validation (non-bool payloads do
  not get coerced).

## [2.61.0-rc.2] - 2026-04-22

Build-hygiene follow-up to `2.61.0-rc.1`. No runtime behaviour changes.

### Changed
- **Docker build context trimmed** — `.dockerignore` now excludes the
  `ui/` dev UI source (215 MB of `node_modules`), `sendspin-cli/`,
  `rnd/`, every `__pycache__/`, `*.pyc`/`*.pyo`, the usual linter/test
  caches, and the dev-screenshot PNG families that weren't already
  covered (`stats-*`, `ru-*`, `ghpages-*`, `social-*`, `landing-*`,
  `config-*`, `mobile-nav-*`). Fresh CI runners no longer pay to ship
  the UI dev tree into the builder.
- **Image payload narrowed** — `Dockerfile` replaces the blanket
  `COPY scripts/ scripts/` with an explicit list of the three scripts
  that actually run inside the container: `translate_ha_config.py`
  (called by `entrypoint.sh` in HA addon mode) and
  `check_sendspin_compat.py` / `check_container_runtime.py` (invoked
  by `release.yml` post-build smoke tests). Eight dev-only scripts
  (`rpi-*.sh`, `proxmox-vm-*.sh`, `generate_ha_addon_variants.py`,
  `release_notes.py`, `translate_landing.py`) are no longer packaged.

### Fixed
- **`__pycache__` no longer leaks into the image** — `/app/routes/`,
  `/app/services/`, and `/app/scripts/` previously shipped stale
  bytecode from the developer's local interpreter runs. Addressed via
  the `.dockerignore` additions above.

## [2.61.0-rc.1] - 2026-04-22

Opt-in experimental sink-recovery flags, connect-path hardening, and
reliability improvements for the standalone-pair flow surfaced by the
Synergy 65 S `AuthenticationCanceled` report (issue #168). Supersedes
the 2.60.5-rc line.

### Added
- **`EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE`** — opt-in flag gating the
  disconnect→2 s wait→reconnect dance in `BluetoothManager` when no sink
  appears after a successful connect. Previously unconditional; the dance
  helps on some headless PipeWire/BlueZ 5.86 setups but hurts others (see
  forum #78, related to #174), so it's now opt-in.
- **`EXPERIMENTAL_PA_MODULE_RELOAD`** — opt-in flag gating the last-resort
  `pactl unload-module / load-module module-bluez5-discover` escalation
  when `bluez_card.*` fails to register. Disruptive (drops every other
  active BT sink), globally throttled to once per 60 s across the bridge,
  and now serialized so two concurrent callers can never run the reload
  back-to-back.
- **`EXPERIMENTAL_PAIR_JUST_WORKS`** — opt-in flag (issue #168) that
  registers bluetoothctl's agent as `NoInputNoOutput` so Secure Simple
  Pairing runs Just-Works (no passkey exchange). Workaround for audio
  sinks that cancel authentication under the default `KeyboardDisplay`
  agent. Read via `load_config()` on every pair attempt — no restart.
- **Post-pair audio-profile sanity check** — if a freshly paired device
  advertises no audio UUIDs (`A2DP`, `HFP`, `Headset`), the bridge now
  surfaces `last_error = "no_audio_profiles_advertised"` on device
  status so the UI can show a targeted banner instead of a generic
  sink-not-found error. Backed by new `bt_dbus._dbus_get_device_uuids`
  and `AUDIO_SINK_UUIDS` constant.
- **Scan-filter drop reasons** — `_classify_audio_capability` in
  `routes/api_bt.py` now returns a machine-readable `reason` label
  (`audio_class_of_device` / `non_audio_class_of_device` / `audio_uuid`
  / `no_audio_class_no_uuid` / `no_class_info_defaults_audio`). Scan
  telemetry aggregates the drop reasons so support can answer "why
  doesn't my speaker show up" without guessing.
- **`services.pulse.cycle_card_profile` / `acycle_card_profile`** —
  helper that cycles `bluez_card.*` `off → a2dp_sink` to force PA to
  re-publish a missing sink without kicking other active BT streams.
  Milder than the module reload, no flag needed.

### Fixed
- **#168 — standalone pair unreliable on slow SSP speakers** — three
  improvements to `_run_standalone_pair_inner`:
  - **Event-driven pair trigger**: `pair <mac>` fires as soon as
    `[NEW] Device <mac>` shows up on scan (typical 1–3 s), replacing
    the fixed 12 s sleep so the peer is still accepting when `pair`
    lands. Falls back to the hard cap if the device never advertises.
  - **Full stdout on FAIL** in debug log (was `out[-800:]`, which
    routinely cut off the passkey/agent prompt needed to diagnose).
  - Optional Just-Works SSP agent (see Added).
- **`_dbus_wait_services_resolved` pre-audio gate** — polls BlueZ
  `Device1.ServicesResolved` (≤10 s) after `Connect()` returns, so
  downstream profile/sink work doesn't race an uninitialized Device1.
  Tri-state return (`True` / `False` / `None`): `None` means "could
  not check" (dbus-python missing or no device path) and the caller
  skips the misleading "did not reach True within 10s" warning.
- **`areload_bluez5_discover_module` — asyncio.CancelledError**
  propagation: the helper now catches `OSError` only, so task
  cancellation unwinds cleanly on shutdown/restart (previously
  suppressed alongside OSError).
- **`areload_bluez5_discover_module` — cooldown burn on failure**:
  `_LAST_BLUEZ5_RELOAD_TS` is now written only after a full
  successful `unload-module` + `load-module`. Trivial failures
  (pactl unavailable, non-zero rc, module not loaded) no longer
  block a later healthy attempt.
- **`areload_bluez5_discover_module` — concurrent caller race**:
  added `_BLUEZ5_RELOAD_IN_PROGRESS` flag under the existing
  `threading.Lock` + `try/finally` so two concurrent callers can't
  both pass the cooldown check and run the reload back-to-back.
- **`_dbus_wait_services_resolved` wait_with_cancel contract**
  (03c4d8a0): the helper now treats `wait_with_cancel` returning
  `True` as "waited uninterrupted" and keeps polling, matching
  `BluetoothManager._wait_with_cancel`'s convention. Previously
  the contract was inverted and the helper exited after the first
  non-True property read.

### Changed
- `_cycle_card_profile_for_mac` docstring now states True only when
  the full off → `a2dp_sink` cycle (including the final switch)
  completes successfully.

### Tests
965+ → 1452 passing. New coverage: scan-filter reasons
(`test_api_bt_scan_filter.py`), event-driven pair + Just-Works agent
+ full-stdout-on-fail, tri-state dbus wait, cooldown-on-success-only,
concurrent reload serialization, cancellation propagation.

## [2.60.5-rc.1] - 2026-04-21

Small UX follow-up to v2.60.3: the opt-in pair-time adapter quiesce checkbox is
now hidden by default behind the experimental-features toggle, since the feature
only helps a narrow corner case (single-adapter + BlueZ 5.78–5.86 regression).

### Changed
- **Pair-time adapter quiesce is now gated behind "Show experimental features"** —
  the "Pause other speakers on same adapter" checkbox in the Bluetooth scan modal
  is hidden by default and only appears when the experimental-features toggle in
  General settings is enabled. No change to the underlying pair flow or API — the
  `quiesce_adapter` flag on `POST /api/bt/pair_new` and `/api/bt/pair` continues
  to work unchanged.

## [2.59.0-rc.2] - 2026-04-17

Second review round on top of rc.1. Feedback from Copilot on PR #158:

### Fixed
- **`services/subprocess_stderr.py`** — `_PORT_NUMBER_RE` widened to `\d{1,5}`
  with an explicit `1..65535` range check so low-range ports (80, 443, …)
  appear in the `lsof -i :<port>` hint and out-of-range numbers fall back to
  the generic hint.
- **`sendspin_client.py`** — `DEFAULT_MAX_ATTEMPTS` imported from
  `services.port_bind_probe` and used for both the probe call and the error
  hint range so tuning the constant in one place keeps them in sync.

## [2.59.0-rc.1] - 2026-04-17

Operational-resilience and security-hardening rollup for issues surfaced from
Raspberry Pi 4 / pipewire-pulse reports (#156, #157) plus the CSP nonce-only
migration tracked as a follow-up from 2.58.0.

### Security
- **CSP `script-src` is nonce-only** — `'unsafe-inline'` removed from the
  `Content-Security-Policy` header. Every inline `on*=` event handler in Jinja
  templates *and* HTML strings produced by `static/app.js` migrated to a
  delegated dispatcher keyed on `data-action` / `data-arg`. `<details>` toggle
  events are handled on the capture phase (non-bubbling, but captures traverse)
  to cover dynamically inserted DOM. New regression test scans shipped
  templates and `app.js` so future PRs can't reintroduce inline handlers.

### Added
- **`services/port_bind_probe.py`** — `is_port_available()` +
  `find_available_bind_port()` host-side TCP bind probe (SO_REUSEADDR, no
  SO_REUSEPORT to avoid false positives). `DEFAULT_MAX_ATTEMPTS=10`.
- **Port auto-shift on EADDRINUSE** — `SendspinClient._start_sendspin_inner`
  preflights the listen port before spawning the daemon subprocess; on
  collision it auto-shifts up to `DEFAULT_MAX_ATTEMPTS` ports and records
  `port_collision: True` + `active_listen_port` on device status. After
  `_MAX_BIND_FAILURES=5` consecutive bind failures the restart loop halts
  (with an `lsof -i :<port>` hint) instead of spinning. Halt state auto-clears
  once the daemon is observed alive.
- **Preflight port-collision warning** at orchestrator startup
  (`bridge_orchestrator.py`).
- **EADDRINUSE stderr classifier** — `services/subprocess_stderr.py` detects
  `errno 98` / `address already in use` / `eaddrinuse` markers and extracts
  the port (1–65535) so the surfaced hint names the actual port.

### Fixed
- **#156 — SinkMonitor log flood**: `services/sink_monitor.py` now diagnoses
  the PA connection failure (`socket-missing` / `permission-denied` /
  `server-not-listening` / `protocol-error` / `unknown`) with an actionable
  hint on the first WARNING, demotes subsequent attempts to DEBUG, and
  self-disables after 3 consecutive initial failures so callers fall back to
  daemon-flag idle detection. Post-success transients use exponential backoff
  5→10→20→40→60s (mirrors `MaMonitor`). `start()` resets state so the monitor
  can be revived after the operator fixes PA.
- **#157 — daemon crash on port collision**: see "Port auto-shift" above.

### Notes
- `find_available_bind_port()` is called with `host="0.0.0.0"` (wildcard) to
  match the daemon's actual bind behaviour — the subprocess receives only
  `listen_port` (no `listen_host`), so probing a specific interface would miss
  collisions on other interfaces.

## [2.58.0-rc.5] - 2026-04-17

### Fixed
- **"Add & Pair" now remembers the adapter the scan used** — after a successful post-scan pair, the new fleet row was rendered with `adapter = default` instead of the controller the pairing actually ran against, so the next restart re-pointed the bond at whichever radio BlueZ happened to consider default. Two layered bugs: (a) the frontend `btAdapterOptions` compared the scan-supplied controller MAC against `a.id` (always `hciN`) and never matched, leaving the `<select>` on "default"; (b) the backend `_run_standalone_pair` passed the raw adapter (`hci0`/`hci1` from the scan result) straight to `bluetoothctl select`, which HAOS and LXC reject with `Controller hci1 not available`, so the pair itself silently ran on the default radio. The dropdown now matches against both `a.id` and `a.mac`, and the pair backend resolves `hciN` → MAC via `_resolve_adapter_to_mac` before any `select` — matching the reset/reconnect fix from rc.4
- **BT Info modal shows only the MAC for devices on the non-default controller** — `/api/bt/info` ran `bluetoothctl info <mac>` with no `select`, so on HAOS / LXC with two adapters (`hci0` + `hci1`) the query went to the BlueZ default. Bonds living on the non-default radio (Yandex mini 2 on `hci1` in prod) returned `Device … not available`, so every field except the MAC was empty in the modal. The helper now accepts an adapter (resolving `hciN` → controller MAC), and both UI call sites forward it — the fleet row reads `.bt-adapter`, the "Already paired" list passes `d.adapters[0]`. When the caller can't supply an adapter, the helper probes every controller in turn and returns the first response with real device fields, so legacy call sites still work

## [2.58.0-rc.4] - 2026-04-17

### Fixed
- **Reset & Reconnect now honours the adapter the device is bonded with** — the `/api/bt/reset_reconnect` backend has always threaded `select <adapter>` through the `remove`, power-cycle, and `pair`/`trust`/`connect` bluetoothctl sessions, but both UI call sites (the configured-fleet row and the "Already paired" list) were calling `resetAndReconnect` without an adapter. On hosts with more than one controller (e.g. `hci0`+`hci1` on the production HAOS VM) the full reset sequence therefore ran against the BlueZ default controller, so bonds living on a non-default radio could never be rebuilt through the UI. The fleet row now reads the adapter from its `<select>`; the paired list passes `d.adapters[0]`. The backend also resolves `hciN` → controller MAC before any `bluetoothctl select`, because HAOS and LXC reject `select hci1` with `Controller hci1 not available` — only the MAC is accepted there
- **"Already paired" list no longer lists ghost devices** — interactive `bluetoothctl` interleaves async discovery notifications (`[CHG] Device <mac> RSSI: …`, `[NEW]/[DEL] Device …`, `[CHG] Device <mac> ManufacturerData.*`) into the same stdout we pipe `devices Paired` through, so the parser was picking up every nearby BLE beacon and showing it as "paired" even when `bluetoothctl info` reported `Paired: no`. `_parse_paired_stdout` now strips the bluetoothctl prompt echo and accepts only bare `Device <mac> <name>` lines; anything behind a `[CHG]`/`[NEW]`/`[DEL]` bracket is treated as noise

## [2.58.0-rc.3] - 2026-04-17

### Added
- **Targeted "enable-linger" hint for headless PipeWire** — preflight audio probe now distinguishes "socket path not mounted" from "socket mounted but server refused the connection". The latter (classic headless Docker/LXC where the user-session PipeWire stopped once SSH disconnected) surfaces a dedicated operator-guidance issue **"Audio server unreachable — enable user lingering"** with the exact fix (`sudo loginctl enable-linger <user>` + reboot) and a link to the docs. The linger hint is gated by `is_ha_addon_runtime()` so HA add-on users — where Supervisor owns audio — still see the generic guidance (fixes #151)

### Fixed
- **Preflight audio reachability is now measured by a real probe** — the previous implementation relied on `services.pulse.get_server_name()` raising on connect failure, but that helper swallows connect errors and returns `"not available"`, so the `system="unreachable"` signal never fired in production. The preflight now performs an explicit `AF_UNIX` connect to the `PULSE_SERVER` socket: `ConnectionRefusedError` → `unreachable` (routes to the linger-specific guidance), `PermissionError`/other `OSError` → generic audio failure with the real error text, so the linger remediation is only offered when it actually applies

## [2.58.0-rc.2] - 2026-04-17

### Added
- **Multi-adapter paired-device management** — `/api/bt/paired` now enumerates every known adapter via `list_bt_adapters()` and queries each with `select <mac>\ndevices Paired`, merging results so each device carries `adapters: [<mac>, ...]`. Previously bonds on a non-default controller were invisible in the UI
- **Per-adapter unpair from the UI** — `/api/bt/remove` accepts optional `adapter_mac` (validated) and, when absent, iterates every adapter so bonds on secondary controllers can finally be removed. The "Already paired" list renders an `hciN`/MAC badge per device so it's clear which controller owns each bond

## [2.58.0-rc.1] - 2026-04-16

### Security
- **SSRF guard on MA auth routes** — `/api/ma/login`, `/api/ma/ha-auth-page`, `/api/ma/ha-silent-auth`, and `/api/ma/ha-login` now validate every user-supplied `ma_url`/`ha_url` through the new `services.url_safety.is_safe_external_url`, which resolves the host via DNS and rejects link-local (cloud metadata / APIPA), reserved, multicast, unspecified addresses, and non-`http(s)` schemes. Loopback and RFC1918 are allowed by default because the bridge is intended to run on home LANs and HAOS — set `SENDSPIN_STRICT_SSRF=1` to also block them (recommended when the bridge is exposed on an untrusted network). In HA addon mode the Supervisor proxy network (`172.30.32.0/23`) and the internal `supervisor`/`hassio`/`homeassistant` hostnames remain allowlisted even in strict mode
- **DNS-rebinding defence** — outbound HTTP from MA auth code now goes through `services.url_safety.safe_urlopen` / `safe_build_opener`, which use `SafeHTTPConnection`/`SafeHTTPSConnection` subclasses that re-check `socket.getpeername()` after the socket connects. Rebinders that return a public IP at validate-time and a link-local/metadata IP at connect-time are rejected before any bytes are sent
- **XSS hardening on `/api/ma/ha-auth-page`** — `ma_url` is injected into an inline `<script>` block; `json.dumps` alone does not escape `</script>`, so a payload containing `</script><script>alert(1)</script>` could have broken out. The server now post-processes the JSON literal with `.replace("</", "<\\/")` before injection
- **MA-reported `ha_url` is re-validated** — `_get_ma_oauth_bootstrap` used to trust the `ha_base` host parsed out of the Music Assistant server's `authorization_url`; a compromised MA could have redirected the browser-less server-side exchange at an internal HA. The parsed `ha_base` now goes through `is_safe_external_url` before any further use
- **Session-bound MFA state** — the second step of `/api/ma/ha-login` (OAuth MFA) no longer trusts `ha_url`, `client_id`, `flow_id`, or `state` from the request body; the server-side `session["_ha_oauth"]` entry stored at step `init` is the only source of truth and is cleared once the flow completes or aborts
- **Supervisor fallback is now opt-in** — when HA Core's `login_flow` is unreachable, the bridge no longer silently falls back to `/auth/login` against the Supervisor API (which does not verify MFA). The fallback must be enabled explicitly with `ALLOW_SUPERVISOR_FALLBACK=1`; when enabled, each use is logged at `WARNING` with "does NOT verify MFA"
- **Logout hardened** — `POST /logout` now requires a valid CSRF token and performs a full `session.clear()` (only `_lockout_client_id` is preserved so brute-force buckets survive). `GET /logout` returns 405 with a small HTML page linking to `/login` so bookmarks and CSRF GETs cannot drop sessions
- **X-Forwarded-For hardening** — rate-limit client identification now picks the rightmost hop that is *not* in `_get_trusted_proxies()`, instead of the spoofable leftmost hop
- **X-Frame-Options: SAMEORIGIN** in standalone (non-HA-addon) mode; HA addon mode still omits it because Ingress needs to frame the UI (CSP `frame-ancestors 'self'` covers that case)

### Fixed
- **500 handler no longer redirects** — `web_interface._handle_500` returns a plain-text `Internal Server Error` response instead of `redirect("/")`, eliminating a potential redirect loop when `/` is itself failing
- **Subprocess stdout stall protection** — `SendspinClient._read_subprocess_output` now wraps `stdout.readline()` in `asyncio.wait_for(timeout=120)`, so a silent-but-alive daemon no longer leaves the reader task blocked forever. Timeouts log at DEBUG and keep polling; a dead subprocess (`returncode != None`) exits the loop cleanly

### Known issues
- CSP still ships with `'unsafe-inline'` because several templates use inline `onclick` handlers. The nonce plumbing is already in place; full migration to `addEventListener` is tracked for a follow-up minor release

## [2.57.0-rc.4] - 2026-04-16

### Changed
- **numpy upgraded to 2.x (no upper pin)** — dropped the previous `numpy<2.0` compatibility cap. sendspin 7 only requires `numpy>=1.26`, but pip now resolves numpy 2.x and a hard compatibility pin would have required a constraint file with `[extras]`, which pip rejects
- **amd64 CPU baseline raised to X86_V2** — numpy 2.x wheels are built with the X86_V2 baseline (SSE3 / SSSE3 / SSE4.1 / SSE4.2). Hosts without these extensions (e.g. QEMU VMs using `cpu: qemu64` or `kvm64`) now fail at startup with `RuntimeError: NumPy was built with baseline optimizations: (X86_V2) but your machine doesn't support: (X86_V2)`. Fix by switching the VM CPU type to `host` (Proxmox: `qm set <vmid> --cpu host`) or any modern named model (e.g. `Haswell`, `Skylake-Client`)
- **armv7 may build numpy from source** — piwheels has no cp312 wheels for numpy 2.x, so the armv7 image will compile it under QEMU. Builds take significantly longer; subsequent releases reuse the cached layer

## [2.57.0-rc.2] - 2026-04-16

### Fixed
- **Album artwork not rendering under HA Ingress** — daemon-reported `artwork_url` points directly at the MA server and fails the same-origin check under `https://ha.example/<slug>_sendspin_bt_bridge_rc/`. UI now runs `artwork_url` through `_getSafeArtworkUrl()` first and falls back to the same-origin signed MA proxy URL (`/api/ma/artwork?...&sig=...`) when a device has MA context
- **Migration warning log spam every 15 s** — HA Supervisor rewrites `/data/config.json` from `options.json` on each restart/poll, so devices with pre-existing negative `static_delay_ms` triggered the "clamping to 0" warning on every `load_config()`. Warnings are now deduplicated per MAC per process, and `scripts/translate_ha_config.py` clamps negatives at the options.json → config.json translation step so the underlying value is fixed at source

## [2.57.0-rc.1] - 2026-04-16

### Changed
- **Upgrade sendspin 5.9.0 → 7.0.0 and aiosendspin 4.4.0 → 5.1.0** — gains DAC-anchored sync (#226), remote per-player delay (#185), multi-server daemon support, and several playback bugfixes
- **`static_delay_ms` now accepts only 0–5000 ms** — negative values are no longer valid. DAC-anchored sync in sendspin 7.0 automatically compensates for audio hardware latency, making the old large negative offsets (−300…−600 ms) unnecessary. Existing negative values are migrated to `0` on first load. Users may fine-tune with small positive values (e.g. 50 ms) if needed
- Default `SENDSPIN_STATIC_DELAY_MS` environment variable changed from `-300` to `0`
- Config schema version bumped to 2 (auto-migrated from v1)

### Fixed
- **Dependency conflict blocking sendspin 7.0.0** — `aiosendspin` updated from 4.4.0 to 5.1.0 (`[server]` extra) to satisfy sendspin 7's `aiosendspin~=5.1` requirement

## [2.56.1-rc.1] - 2026-04-13

### Fixed
- **Sourceplugin metadata mixing MA data from wrong track** — when daemon provides track title but not artist/album/artwork (typical for sourceplugin/ynison), the UI was falling back to MA now-playing for those fields, showing metadata from a completely different song. Now suppresses MA fallback for artist, album, and artwork when daemon already has a track title, preventing cross-track metadata mixing

## [2.56.0-rc.3] - 2026-04-13

### Fixed
- **HA addon 502 on ingress** — `INGRESS_PORT` is not an env var; Supervisor communicates the dynamic port via its REST API. Replaced env var lookup with Supervisor API query (`/addons/self/info`) to read the assigned `ingress_port`

## [2.56.0-rc.2] - 2026-04-13

### Fixed
- **Incorrect track metadata with sourceplugin providers** — when playing via sourceplugin (e.g. Yandex ynison), MA now-playing returned metadata from its own queue item instead of the actual playing track. Changed metadata priority in `_getDeviceNowPlayingState()` and `_getListTrackAlbum()` to daemon-first with MA fallback, matching the existing correct behavior in list view. Affects track title, artist, album, and artwork in all expanded/card views

## [2.56.0-rc.1] - 2026-04-13

### Fixed
- **HA addon ingress port conflict with Matter/Thread** (#138) — switched all addon channels from hardcoded `ingress_port` (8080/8081/8082) to dynamic `ingress_port: 0`. HA Supervisor now auto-assigns a free port, eliminating conflicts with other host-network addons. Channel defaults retained as fallback for older Supervisor versions

## [2.55.2-rc.1] - 2026-04-07

### Fixed
- **Connection errors not surfaced in UI** (#134) — `ClientConnectorError` from daemon subprocess was logged as WARNING but never shown in device status. Added `_connection_watchdog()` in BridgeDaemon (sets `last_error` after 30 s) and consecutive error counter in `SubprocessStderrService` (surfaces after 3+ repeated failures)
- **Generic "lost bridge transport" guidance for port mismatch** (#134) — when transport is down due to connection errors, recovery assistant now shows specific `sendspin_port_unreachable` issue with guidance to check `SENDSPIN_PORT`, instead of generic "restart" advice
- **Stale metadata reconnect timeout too short** (#134) — increased `_STALE_RECONNECT_READY_TIMEOUT` from 30 s to 120 s; added retrigger task that fires reconnect once daemon eventually connects, preventing permanent volume control loss

### Added
- **Sendspin port auto-probe** (#134) — when `SENDSPIN_PORT` is default (9000) and the configured host is explicit, the bridge now TCP-probes candidate ports (9000, 8927, 8095) before connecting. If an alternative port responds, it is used automatically with a WARNING log

## [2.55.0-rc.12] - 2026-04-06

### Changed
- **Unified branding** — all logos, favicons, and addon assets replaced with the landing page wave-bridge design (two pillars + three wave curves); color differentiation preserved across channels; total asset size reduced from ~310 KB to ~55 KB

## [2.55.0-rc.11] - 2026-04-06

### Changed
- **HA addon icons redesigned** — replaced bridge+equalizer icon with landing page logo (two pillars with three wave curves); color differentiation preserved: stable=teal-purple, rc=gold, beta=red; total icon size reduced from 316 KB to 80 KB

## [2.55.0-rc.10] - 2026-04-06

### Fixed
- **Subprocess crash on PipeWire** — keep `libasound2-plugins` (ALSA→PulseAudio bridge) which provides `libasound_module_pcm_pulse.so` required by sounddevice/PortAudio to discover audio sinks; removing it caused "No audio output device found" crash loop

## [2.55.0-rc.9] - 2026-04-06

### Changed
- **Docker image −51%** (916 → ~450 MB) — force-remove transitive FFmpeg/GStreamer/codec deps pulled by PulseAudio on amd64/arm64 (pactl works without them); strip debug symbols from native .so files; remove unused Python stdlib modules (ensurepip, idlelib, lib2to3, pydoc_data, turtledemo, test)

## [2.55.0-rc.7] - 2026-04-06

### Changed
- **Docker image size −37%** (916 → ~580 MB) — removed redundant system FFmpeg libraries on amd64/arm64; PyAV wheels bundle their own FFmpeg in `av.libs/`. System FFmpeg retained for armv7 only (compiled from source)
- **pip package cleanup** — strip `__pycache__`, numpy test suite, pygments, pip from runtime image

## [2.55.0-rc.6] - 2026-04-06

### Fixed
- **NumPy crash on older CPUs** — reverted numpy constraint from `<3.0` back to `<2.0`; numpy 2.x requires X86_V2 baseline (POPCNT/SSE4.2) which is unavailable on QEMU `qemu64` and older physical CPUs, causing `RuntimeError: NumPy was built with baseline optimizations (X86_V2)` in daemon subprocess

### Changed
- **Dependency updates** — `dbus-fast` 4.0.0→4.0.4 (D-Bus performance improvements), `ruff` 0.11.13→0.15.8 (linter update)
- **CI updates** — `docker/build-push-action` v6→v7 (Node 24), `actions/download-artifact` v4→v8 (hash enforcement), `actions/upload-pages-artifact` v3→v4

## [2.55.0-rc.5] - 2026-04-06

### Changed
- **Dependency updates** — `dbus-fast` 4.0.0→4.0.4 (D-Bus performance improvements), `numpy` <2.0→<3.0 (widen compatibility), `ruff` 0.11.13→0.15.8 (linter update)
- **CI updates** — `docker/build-push-action` v6→v7 (Node 24), `actions/download-artifact` v4→v8 (hash enforcement), `actions/upload-pages-artifact` v3→v4

## [2.55.0-rc.4] - 2026-04-06

### Fixed
- **Config download 404 in HA addon ingress mode** — hardcoded `/api/config/download` path in the download button bypassed the ingress `SCRIPT_NAME` prefix; now uses `API_BASE` like all other download endpoints

### Improved
- **Auto-expand device detail row on CTA navigation** — clicking a "Configure" link from onboarding or guidance now auto-expands the device detail row before highlighting it

## [2.55.0-rc.3] - 2026-04-06

### Changed
- **Power save delay in minutes** — `power_save_delay_seconds` renamed to `power_save_delay_minutes` across config, UI, API, and HA addon schemas. Default: 1 min (was 30 s), max: 60 min. Auto-migration converts existing seconds values to minutes on startup.

## [2.55.0-rc.2] - 2026-04-06

### Fixed
- **Idle mode dropdown unstyled** — added `.bt-detail-row select` CSS rules matching existing input styling (base, focus, disabled, mobile breakpoints)

## [2.55.0-rc.1] - 2026-04-07

### Added
- **Per-device idle mode** — new `idle_mode` enum per Bluetooth device replaces the two independent `keepalive_interval` / `idle_disconnect_minutes` settings. Four modes:
  - `default` — no action when idle; speaker's own hardware timer decides
  - `power_save` — suspend PA sink after configurable delay (`power_save_delay_minutes`, 0-60, default 1); releases A2DP transport so speaker can sleep while BT stays connected; auto-resumes on next play
  - `auto_disconnect` — full BT disconnect + daemon→null-sink after `idle_disconnect_minutes` (existing standby behavior)
  - `keep_alive` — stream periodic infrasound bursts at configurable interval (existing keepalive)
- **Infrasound keepalive** — keepalive bursts now use a 2 Hz sine wave at -50 dB instead of pure digital silence. Below human hearing threshold but non-zero PCM data keeps A2DP transport active on speakers that ignore digital silence.
- **PA sink suspend/resume** — new `asuspend_sink()` / `suspend_sink()` helpers in `services/pulse.py` for the power_save mode, with pulsectl + pactl fallback.
- **Status API** — `idle_mode` and `bt_power_save` fields are now exposed in `/api/status` per-device responses.

### Changed
- **Legacy UI** — device detail row now shows a single "Idle mode" dropdown instead of two separate numeric inputs; mode-specific fields (delay, standby minutes, keepalive interval) are shown/hidden based on selected mode.
- **HA addon schemas** — `idle_mode` and `power_save_delay_minutes` options added to all three addon configs (stable, beta, rc).
- **Config migration** — old configs with `keepalive_interval > 0` auto-migrate to `idle_mode: keep_alive`; `idle_disconnect_minutes > 0` to `auto_disconnect`; both zero to `default`. Explicit `idle_mode` values are never overwritten.

## [2.54.0-rc.6] - 2026-04-04

### Fixed
- **Onboarding regresses during standby** — devices in idle-standby are now treated as "logically connected" so onboarding checks and checkpoints don't show incomplete state when the bridge intentionally disconnected BT to save power
- **Idle timer not re-armed after wake** — SinkMonitor fires `on_idle` while `bt_standby` is still True during wake flow; now re-checks sink state after clearing standby and re-arms the idle timer

## [2.54.0-rc.5] - 2026-04-04

### Fixed
- **Idle timer not re-armed after wake** — SinkMonitor fires `on_idle` while `bt_standby` is still True during wake flow; `_on_sink_idle()` returns early and the timer never restarts. Now re-checks sink state after clearing standby and re-arms the idle timer.

## [2.54.0-rc.4] - 2026-04-03

### Fixed
- **Docker update command** — modal now shows `docker compose pull && docker compose up -d` instead of just `docker pull` which didn't recreate the running container

## [2.54.0-rc.3] - 2026-04-03

### Fixed
- **Idle standby broken** — pulsectl's `EnumValue` supports `== 'suspended'` but not `int()` or `== 2`; sink state always classified as "unknown", preventing idle timer from starting. Now uses string equality with int fallback.

## [2.54.0-rc.2] - 2026-04-03

### Fixed
- **Logs endpoint empty in Docker** — `from sendspin_client import _ring_log_handler` created a second module instance with empty buffer (main runs as `__main__`); now reads via `sys.modules['__main__']`

## [2.54.0-rc.1] - 2026-04-03

### Fixed
- **Restart banner stuck** — `sawRuntimeRestart` now set on successful `/api/restart` response; poll delayed past 500 ms kill window; 60 s safety timeout auto-clears banner
- **Restart fails under S6 overlay** — `PermissionError` when UID 1000 can't signal root PID 1; falls back to `os.kill(os.getpid())` so S6 supervise restarts the child
- **Update check fails on OpenSSL 3.5** — post-quantum ML-KEM key exchange produces oversized TLS Client Hello (1569 B) that middleboxes drop; GitHub API calls now pin `prime256v1` ECDH curve

### Removed
- **`handoff_mode` device option** — unused since v2.53; removed from config schema, migration, orchestrator, status snapshot, and all tests

## [2.53.0-rc.2] - 2026-04-03

### Added
- **WebSocket heartbeat for server-initiated connections** — daemon now sends 30 s ping/pong on the WebSocket server side, matching MA's client-side heartbeat; prevents idle connection drops through proxies, firewalls, and Docker bridge networks (#120, music-assistant/support#4598)

### Removed
- Dead fallback methods `_ma_monitor_says_playing()` and `_event_history_says_playing()` — defined and tested but never called from production code; `SinkMonitor` is the sole authority for idle detection since 2.53.0

## [2.53.0-rc.1] - 2026-04-02

### Added
- **PA sink state monitoring** — PulseAudio/PipeWire sink state (`running`/`idle`/`suspended`) is now the sole authority for idle disconnect, replacing the fragile 3-tier daemon-flag + MA-monitor + event-history system (#120)
- `SinkMonitor` module: subscribes to PA sink events via `pulsectl_asyncio`, tracks state for all Bluetooth sinks, fires callbacks on `running ↔ idle` transitions
- Initial sink scan on PA connect/reconnect to populate state cache — prevents stale data after PA connection loss

### Fixed
- **Recurring speaker disconnection** during active playback — the old idle guard relied on daemon flags (`audio_streaming`, `playing`) which reset on MA-forced daemon reconnects every ~55 min (#120)
- Solo player (not in a sync group) standby/wake: `_ma_monitor_says_playing()` and `_check_group_auto_wake()` now fall back to `player_id` when `group_id` is None
- Thread safety: `_idle_timer_task` now protected by `_idle_timer_lock` — prevents leaked timers from concurrent access by asyncio event loop and Flask/Waitress threads
- Firing-time safety guard: idle timer re-checks `bt_standby`, `bt_waking`, `keepalive_enabled`, and cached PA sink state before entering standby
- SinkMonitor lifecycle: properly stopped on shutdown, startup failure, and signal handling

## [2.52.5-rc.1] - 2026-04-02

### Fixed
- Solo player (not in a sync group) standby/wake: `_ma_monitor_says_playing()` and `_check_group_auto_wake()` now fall back to `player_id` when `group_id` is None, fixing idle-standby during active playback and auto-wake for ungrouped devices

## [2.50.0-rc.1] - 2026-03-26

### Changed
- Bump websockets 13.1 → 16.0 (async API migrated to `websockets.asyncio.client`)
- Bump waitress 2.1.2 → 3.0.2
- Bump pytest-asyncio to <2.0.0
- Bump CI actions: github-script 8, setup-node 6, upload-artifact 7, deploy-pages 5, setup-buildx-action 4

## [2.48.1-rc.4] - 2026-03-25

### Fixed
- Avoid false `lost bridge transport` states after a successful replacement reconnect. The bridge now publishes `server_connected` only after the new Sendspin websocket handshake succeeds, so the old session's disconnect callback cannot overwrite the fresh connection state back to disconnected.

## [2.48.1-rc.3] - 2026-03-25

### Fixed
- Avoid false recovery/disconnected UI states during planned Music Assistant metadata reconnects. The bridge now marks this as a dedicated `ma_reconnecting` transition, so idle speakers without an active audio stream show a benign reconnecting state instead of `lost bridge transport` or `Music Assistant unavailable`.

## [2.48.1-rc.2] - 2026-03-25

### Fixed
- Avoid false `lost bridge transport` guidance while audio is already playing. Recovery and operator guidance now treat active audio streaming as authoritative during brief Sendspin control reconnects, so transient `server_connected=false` windows no longer raise a transport-loss warning when the speaker is still streaming.

## [2.48.1-rc.1] - 2026-03-25

### Fixed
- Avoid `sendspin.audio` callback crashes after ALSA underrun / re-anchor recovery. The bridge now guards against stale cached output-frame state inside the subprocess runtime so a reused frame from an older format or correction cycle is reset instead of exploding with `ValueError: memoryview assignment: lvalue and rvalue have different structures`.

## [2.48.0-rc.11] - 2026-03-25

### Fixed
- Make native Sendspin `shuffle`/`repeat` buttons feel immediate again. The web UI now applies the same kind of optimistic local state update it already used for Music Assistant queue commands, instead of waiting several seconds for the backend status round-trip before changing the button state.

## [2.48.0-rc.10] - 2026-03-25

### Fixed
- Avoid the remaining startup race in stale `device_info` recovery. Automatic metadata reconnects are now held back for an additional startup grace window after the player first becomes ready, so they do not interrupt the initial post-restart handshake and leave the speaker idling.

## [2.48.0-rc.9] - 2026-03-25

### Fixed
- Avoid intermittent post-restart idle/stuck players when Music Assistant reports stale `device_info` before the Sendspin subprocess is fully ready. Stale-metadata reconnects are now deferred until the player subprocess is running and connected, instead of being sent too early and getting lost during startup.

## [2.48.0-rc.8] - 2026-03-25

### Fixed
- Avoid false Bluetooth device removal during `Save & Restart` when the default adapter is represented as a missing value in the saved config but as an empty string in the web UI payload. Default adapter values are now normalized before the config save path decides whether a device was moved to another adapter.

## [2.48.0-rc.7] - 2026-03-25

### Fixed
- Persist the live Bluetooth sink volume during graceful shutdown, so `Save & Restart` restores the last user-set speaker volume instead of falling back to the sink default on the next startup.

## [2.48.0-rc.6] - 2026-03-25

### Fixed
- Avoid false `repair required` states after bridge restarts when BlueZ temporarily reports the speaker device as unavailable. The bridge now treats the pairing state as unknown in that window, retries a normal reconnect first, and only falls back to re-pair when BlueZ explicitly reports `Paired: no`.

## [2.48.0-rc.5] - 2026-03-25

### Fixed
- Complete sendspin 5.8.0 audio API compatibility. The bridge now resolves `query_devices`, `parse_audio_format`, and `detect_supported_audio_formats` from either `sendspin.audio_devices` (new layout) or legacy `sendspin.audio`, and adapts to the new `detect_supported_audio_formats(audio_device)` signature.
- Restore diagnostics and demo-mode PortAudio device reporting with the new sendspin audio module layout.
- Make sendspin compatibility tests order-independent by cleaning up mocked audio modules consistently.

### Changed
- Pin direct runtime dependencies in `requirements.txt` to the CI-validated versions so future upstream releases do not silently change the runtime API surface on new installs or image rebuilds.

## [2.48.0-rc.4] - 2026-03-25

### Fixed
- Restore daemon startup with sendspin builds that no longer export `parse_audio_format` from `sendspin.audio`. Preferred format resolution now falls back to `detect_supported_audio_formats()` instead of crashing the subprocess on import.

## [2.48.0-rc.3] - 2026-03-25

### Added
- Separate `RECOVERY_BANNER_GRACE_SECONDS` setting to keep recovery banners hidden for a configurable delay after the startup lockout/finalizing page is lifted.

### Changed
- Startup finalizing grace now defaults to `5` seconds, and the new recovery-banner grace defaults to `15` seconds.
- Default `PULSE_LATENCY_MSEC` for new installs is now `600`, and newly added Bluetooth devices default to `static_delay_ms = -300`.

## [2.48.0-rc.2] - 2026-03-25

### Added
- Cross-bridge duplicate device detection. When multiple bridge instances (e.g. stable + RC addons) share the same host, the bridge now detects devices already registered under another instance at startup and during BT scans.
- Startup warning + recovery banner when a configured device conflicts with another bridge (via existing RecoveryIssue / operator guidance system).
- BT scan results annotated with ⚠ warning chip when a discovered device is already registered on another bridge. Add/Pair buttons show a confirmation prompt.
- `DUPLICATE_DEVICE_CHECK` config option (default: enabled) to control cross-bridge detection.

## [2.48.0-rc.1] - 2026-03-25

### Added
- Native Sendspin transport commands via Controller role (`POST /api/transport/cmd`). Play, pause, stop, next, previous, shuffle, repeat, and volume commands are sent directly over the Sendspin WebSocket — bypassing the Music Assistant REST API for lower latency.
- Extended metadata forwarding from Sendspin protocol: album, album artist, artwork URL, year, track number, shuffle state, and repeat mode are now included in device status.
- Controller state listener: `supported_commands`, `group_volume`, and `group_muted` are forwarded from the MA server's controller role updates.
- Web UI uses native shuffle/repeat/album/artwork as fallback when Music Assistant API is unavailable.
- Web UI transport buttons prefer native Sendspin commands when supported, falling back to MA queue commands for seek and when native transport is unavailable.

## [2.46.1-rc.7] - 2026-03-24

### Fixed
- Demo-mode restart emulation now marks startup progress as `stopping` before resetting runtime config, and the restart regression test now waits for the explicit `Demo restart complete` state, removing the CI race that could leave the test observing `stopping` instead of `ready`.
- The lint workflow now pins Ruff to the same formatter line used by the repository hooks, avoiding spurious CI-only `ruff format --check` drift.

## [2.46.1-rc.6] - 2026-03-24

### Changed
- The restart finalizing grace period now defaults to `10` seconds and is configurable from the bridge settings (and Home Assistant add-on options), so the UI can unlock sooner after the backend reports ready.

### Fixed
- Disconnected-device recovery banners now promote `Release Bluetooth` to the primary action while auto-reconnect is already in progress, keeping the top banner aligned with the inline recommendation.

## [2.46.1-rc.5] - 2026-03-24

### Changed
- Docker update dialogs now show the manual pull/redeploy guidance directly inside the modal, including a one-click copy action for the exact `docker pull` command.
- Restart banners and zero-device startup placeholders now turn the long `Startup 90%` tail into a live device-restore summary, showing how many speakers are ready and how many are still reconnecting or waiting for Bluetooth, a sink, or Sendspin.

### Fixed
- Demo mode now keeps temporary config writes inside a writable demo-only config path and uses an explicit fake restart hook, so `Save and Restart` really resets temporary fleet additions instead of trying to touch `/config`.
- Demo Bluetooth scans now hide MAC addresses that are already present in the configured fleet or already paired inventory, so scan results only show genuinely new demo devices.

## [2.46.1-rc.4] - 2026-03-24

### Changed
- Bluetooth scan empty states now suggest a practical recovery path when the target device does not appear: retry the scan, reboot the Bluetooth adapter, and finally reboot the host if needed.

## [2.46.1-rc.3] - 2026-03-24

### Changed
- Music Assistant syncgroup cache refreshes now log at `INFO` only when the cached group mapping actually changes, while unchanged periodic refreshes stay in `DEBUG`.
- Latency tuning guidance now sends multi-device setups without per-device static delays straight to the device fleet delay settings instead of the global PulseAudio latency control.
- List-view secondary actions (`Reconnect`, `Release`, `Disable`) now reuse the shared action-button styling, stay vertically centered against the player card, and remain visually muted until hover.

### Fixed
- Bluetooth standalone pairing/reconnect flows now clear stale device state, wait to `trust` a device until pairing really succeeds, and serialize scan/pair/reset operations so BlueZ agent registration conflicts no longer break pairing.
- List-view cards now hide row-level transport controls when sendspin transport is unavailable, suppress empty playback rails, and keep the remaining playback rail centered when Music Assistant data is unavailable.

## [2.46.1-rc.2] - 2026-03-24

### Changed
- Bluetooth pairing failure logs now surface the most useful `bluetoothctl` / BlueZ reason (for example `Failed to pair: org.bluez.Error.ConnectionAttemptFailed`) instead of only a truncated output tail, while keeping the longer raw output in `DEBUG`.

## [2.46.1-rc.1] - 2026-03-24

### Changed
- Header runtime/version badges are now visually distinct again: runtime uses a standard passive badge, while the current version keeps a standard interactive badge without reading like a full button.
- Routine config reloads are now quieter in logs. Successful `load_config()` calls only log at `INFO` on first startup load, while later reloads and runtime-state-only config writes stay in `DEBUG`.

## [2.46.0-rc.3] - 2026-03-23

### Changed
- Docker and Raspberry Pi images now keep container init/root setup for Bluetooth and D-Bus, but automatically re-exec the bridge process as `AUDIO_UID` for user-scoped host audio sockets. This fixes the common Raspberry Pi root-vs-user PulseAudio/PipeWire mismatch without requiring a global Compose `user:` override.
- Startup diagnostics, the Raspberry Pi pre-flight checker, and Docker docs now distinguish init UID vs app UID, explain the new split-privileges model, and treat a global Compose `user:` override as an older-image diagnostic fallback instead of the preferred deployment path.

## [2.46.0-rc.2] - 2026-03-23

### Changed
- Docker/Raspberry Pi startup diagnostics now report the runtime UID/GID, selected host audio socket path, socket ownership/mode, and a live `pactl info` probe result so PipeWire/PulseAudio access problems are much easier to diagnose from container logs.
- The Raspberry Pi pre-flight checker and Docker docs now explain `AUDIO_UID` more clearly, include copy-paste audio troubleshooting commands, and document a temporary `user:` override test for confirming user-scoped PipeWire/PulseAudio UID mismatches.

## [2.46.0-rc.1] - 2026-03-23

### Added
- Bridge-backed Bluetooth devices can now carry stable room metadata (`room_name`, `room_id`, source/confidence) and expose it through status snapshots, making Music Assistant / Home Assistant / MassDroid room mapping much easier to reason about.
- Device snapshots now include a compact `transfer_readiness` contract so operators and automations can see whether a speaker is truly ready for a fast room handoff.

### Changed
- Per-device settings now support an explicit `handoff_mode`, with `fast_handoff` reusing the existing keepalive path to keep selected speakers warmer for transfer-heavy room workflows.
- Runtime device events are now enriched with room and readiness context, and the web UI surfaces new room / transfer badges plus manual room assignment controls in device settings.
- Home Assistant add-on config sync/translation now preserves the new room and handoff fields across supervisor round-trips and restarts.

## [2.45.0-rc.3] - 2026-03-23

### Added
- Diagnostics recovery timeline now retains a deeper event window and exposes advanced severity, scope, source, and window filters for power-user trace review.

### Changed
- Home Assistant area-based naming suggestions for `Bridge name` and Bluetooth adapter names are now toggleable, while still defaulting to enabled in HA add-on mode.

## [2.45.0-rc.2] - 2026-03-23

### Changed
- The onboarding checklist now stays out of the main notice stack on non-empty installs until the operator expands it, so recovery guidance owns the top-level next-action surface during day-to-day runtime issues.
- Grouped guidance actions now show an affected-device preview before bulk reconnect, Bluetooth-management, or safe-check reruns are queued.
- Recovery issue pills now collapse dense attention states into a calmer `+N more` summary, and row-level blocked hints suppress duplicate remediation copy when the same action is already explained by top-level guidance.

## [2.45.0-rc.1] - 2026-03-23

### Added
- Home Assistant ingress sessions can now fetch the HA area registry into the config UI, so `Bridge name` offers one-click room suggestions instead of requiring manual retyping.
- Bluetooth adapter settings now support optional HA area mapping by adapter MAC, including exact device-registry matches and a `Use area name` shortcut for adapter custom names without touching existing names automatically.

## [2.44.0-rc.2] - 2026-03-23

### Added
- Music Assistant runtime can now be reloaded without restarting the whole bridge: saving a new MA URL/token reuses the running process, reloads monitor credentials, and re-runs MA group discovery through the new `POST /api/ma/reload` path.

## [2.44.0-rc.1] - 2026-03-23

### Changed
- Diagnostics downloads and bugreport text now include a plain-text recovery timeline summary, so support bundles capture the recent reconnect/sink history without requiring the separate CSV export.
- Music Assistant discovery now prioritizes Home Assistant add-on candidates, preserves the discovery source/summary in the API payload, and steers missing-URL onboarding toward retrying discovery before manual MA setup.
- Device capability metadata now exposes dependency chains and recommended actions, letting onboarding, recovery guidance, and blocked controls reuse the same remediation contract.

### Fixed
- Blocked device controls no longer rely on hover-only titles: cards and expanded list rows now render visible compact hints with dependency copy and inline remediation actions for touch/mobile operators.
- Latency guidance can now offer the recommended PulseAudio setting directly from onboarding/operator guidance instead of forcing a detour into full diagnostics first.

## [2.43.0-rc.5] - 2026-03-23

### Fixed
- Onboarding step indicators now stay circular in the responsive/mobile layout too, instead of reverting to rounded-square markers under the compact CSS override.

## [2.43.0-rc.4] - 2026-03-23

### Fixed
- The expanded onboarding banner now renders the full checklist instead of truncating it to five visible items, so the step list matches the seven-step progress indicator shown to operators.

## [2.43.0-rc.3] - 2026-03-23

### Changed
- Onboarding now exposes a staged `foundation → first speaker → Music Assistant → tuning` journey in addition to the dependency-ordered checklist, so first-room setup reads as a clearer guided flow instead of only a flat status list.
- Recovery diagnostics now include rerunnable safe checks, richer latency guidance with current/recommended values and presets, and a chronological recovery timeline with CSV export.
- Roadmap and TODO docs were synced with the real v2 state, retiring the stale standalone/LXC auto-update backlog item and narrowing the remaining pre-v3 gaps to the true UX/productization work.

## [2.43.0-rc.2] - 2026-03-23

### Changed
- Refined the onboarding checklist flow connector so the line cleanly links step indicators, feels closer to the rest of the UI chrome, and no longer shows through the step indicator itself.

## [2.43.0-rc.1] - 2026-03-23

### Changed
- Added a normalized bridge/device state model across `/api/status`, device snapshots, onboarding, recovery, and operator guidance so runtime substrate, configuration intent, transport/sink health, and recovery hints are derived once and exposed consistently.
- Extracted shared device health and capability derivation into reusable services, including machine-readable blocked-reason metadata and guidance issue context (`layer`, `priority`, `reason_codes`) for future UI/status extensions.

## [2.42.4-rc.5] - 2026-03-23

### Fixed
- Mixed onboarding states are now explained more clearly when a saved speaker is disabled and no paired Bluetooth speaker is available: the UI now prioritizes pairing/rediscovery first, surfaces a visible `Scan for speakers` action, and avoids the misleading `All devices disabled` summary for that case.

## [2.42.4-rc.4] - 2026-03-23

### Changed
- Onboarding now follows the real bridge dependency hierarchy: runtime host access, Bluetooth control, audio backend health, bridge-managed device availability, sink readiness, Music Assistant integration, and only then latency tuning.

### Fixed
- Neutral operator states like `all devices disabled` or `all devices released` no longer demote higher-priority infra failures; if runtime, Bluetooth, or audio access is broken, guidance keeps that layer as the current recovery step instead of pushing operators to lower-level device actions first.

## [2.42.4-rc.3] - 2026-03-23

### Fixed
- Standalone/LXC installs now persist the exact installed release ref and expose it as the runtime version, so RC-channel deployments continue to see newer RC builds instead of collapsing to the stable release line after an update.

## [2.42.4-rc.2] - 2026-03-23

### Changed
- Operator guidance now treats Bluetooth adapter access as a top-level dependency: when preflight cannot see a controller, the header, banner, and onboarding card all push operators to restore adapter access before trying to re-enable speakers.

### Fixed
- Standalone RC updates now finish cleanly in the UI when the backend reports the upgraded release line (`2.42.4`) instead of the full prerelease ref (`2.42.4-rc.2`), preventing `Update in progress` from getting stuck after a successful upgrade.
- The Bluetooth scan flow no longer crashes while rendering scan outcomes, and the guidance/tests around disabled devices are now deterministic across hosts with different local Bluetooth preflight state.

## [2.42.4-rc.1] - 2026-03-23

### Changed
- The Bluetooth scan modal now keeps active scans explicit even after dismissal: closing the dialog leaves the scan running in the background, the main launcher switches into an `Open active scan` state, and reopening the modal rehydrates the current progress/results instead of silently starting over.

### Fixed
- The Bluetooth scan modal now behaves like a real dialog for keyboard users by trapping Tab navigation inside the overlay, moving focus into the modal on open, and restoring focus to the opener on close.
- Scan and pair job polling now share the same hardened async path, so non-OK responses surface cleanly in the UI and pair failures use in-app toast/status feedback instead of blocking browser alerts.
- Scan result rows no longer advertise false whole-row click affordances; interaction stays button-driven and passive rows read as informational rather than broken.

## [2.42.3-rc.3] - 2026-03-22

### Added
- The bug report dialog now pre-fills an editable description generated from attached diagnostics, summarizing recent errors, Bluetooth/device health, daemon status, and Music Assistant connectivity so issue reports start with more useful context.

### Fixed
- The `Auto-get token on UI open` Music Assistant setting is now hidden outside Home Assistant add-on mode, matching the runtime behavior where silent token bootstrap only works through HA ingress.

## [2.42.3-rc.2] - 2026-03-22

### Changed
- Onboarding guidance now separates status from disclosure more clearly: the header keeps a passive setup-status badge, while checklist visibility uses an explicit `Show checklist` / `Hide checklist` control and a collapsed summary state in the notice stack instead of disappearing completely.
- The Music Assistant configuration flow is now easier to re-enter after initial setup: the connection-status card owns the `Reconfigure` action, and the sign-in/token section stays hidden until reconfiguration is explicitly requested.

### Fixed
- The onboarding checklist toggle now updates its `Show` / `Hide` state immediately when clicked instead of waiting for the next background status refresh.
- Guidance and banner CTAs that send operators back to Music Assistant token setup now open the section directly in reconfigure mode so the auth controls are visible right away.

## [2.42.3-rc.1] - 2026-03-22

### Fixed
- The Bluetooth scan modal now keeps discovered-device badges inline after the device name, making dense result lists more compact without losing badge context.
- The `Bluetooth → Paired devices` inventory layout is corrected again: the subtitle stays on one line, the inner `Already paired devices` header/count no longer collapses, and the `Info`, `Reset & Reconnect`, and remove actions stay aligned on the right side of each row.

## [2.42.2-rc.7] - 2026-03-21

### Fixed
- Standalone Home Assistant login against Music Assistant add-ons now completes again after TOTP by falling back to direct HA login flow, resolving MA ingress through HA Supervisor APIs, and creating the final MA token with an `ingress_session` cookie instead of a plain HA bearer token.

## [2.42.2-rc.6] - 2026-03-21

### Fixed
- Home Assistant login failures against Music Assistant now return the actual MA-side bootstrap reason when HA OAuth is unavailable, and the UI guidance now tells operators to switch to direct Music Assistant authentication when HA login is not configured there.

## [2.42.2-rc.5] - 2026-03-21

### Changed
- The Bluetooth scan dialog now follows the shared compact modal system instead of the older bug-report shell, with a more consistent accent header, modal layout, scan controls, progress section, and results framing.
- Bluetooth scan and paired-device actions now speak the same design language as the rest of the interface, including the bluetooth-icon `Tools` trigger in device rows and a simpler static paired-devices header without leftover disclosure styling.

### Fixed
- The scan modal copy now explains the actual operator workflow — choose an adapter, scan nearby devices, then add or pair speakers — instead of describing the internal implementation of the page.

## [2.42.2-rc.4] - 2026-03-21

### Added
- Onboarding now recognizes when every configured speaker has been manually released and offers direct reclaim actions so playback can be resumed without hunting through the configuration screens first.

### Changed
- The compact UI now exposes a clearer shared design-system layer: spacing, typography, focus-ring, layout, count-badge, and action-menu primitives are reused across notice, configuration, toolbar, and guidance surfaces instead of being defined as scattered local overrides.
- Configuration headers, notice copy blocks, and unsaved-count indicators now share the same structural shells, improving hierarchy and reducing visual drift across dashboard and settings flows.

### Fixed
- Guidance cards that opt into `show_by_default` now auto-open consistently from the header entry point instead of only doing so for the empty-state scenario.

## [2.42.2-rc.3] - 2026-03-21

### Changed
- Badge and chip styling now follows a much more unified system across the live dashboard, device fleet, scan progress, onboarding, and recovery surfaces, reducing visual drift between list, grid, and configuration views.

### Fixed
- Interactive and passive badges now use more consistent borders, hover feedback, and cursor behavior throughout the interface, and the `BT tools` menu now matches the compact control typography used elsewhere.

## [2.42.2-rc.2] - 2026-03-21

### Added
- The Bluetooth scan modal now exposes adapter selection, an explicit audio-only filter, and a dedicated rescan action so multi-adapter discovery is easier to control.

### Changed
- Bluetooth discovery now reports richer scan metadata to the frontend, letting the modal show timed progress, countdown state, and clearer result context without turning the workflow into a permanent page block.

### Fixed
- Scan modal results now stay aligned with the selected discovery scope, and non-audio Bluetooth candidates are surfaced more honestly when the audio-only filter is disabled.

## [2.42.2-rc.1] - 2026-03-20

### Changed
- The compact UI system is now much more consistent across the live app: primary/secondary/icon actions, media transport controls, table-like rows, and empty states now follow a shared visual language instead of mixing several older styles.
- Configuration, diagnostics, discovery, and device list surfaces now use denser data-row and placeholder treatments, keeping the current information architecture while making the interface feel more coherent and Home Assistant-aligned.
- The login screen now follows the same refreshed compact styling as the main application, reducing the visual jump between authentication and the dashboard.

### Fixed
- Demo mode regains compatibility with the refreshed UI preview workflow, so local demo validation continues to work against the current Bluetooth manager behavior.

## [2.42.0-rc.23] - 2026-03-20

### Added
- Diagnostics cards can now copy their section content to the clipboard for support workflows and reveal raw payload details on demand for expert troubleshooting.

### Changed
- Grid view playback cards now use larger now-playing artwork thumbnails so album art fills more of the media block instead of leaving extra empty space above and below.
- Diagnostics now opens with a clearer `Overview` layer and a separate collapsible `Advanced diagnostics` layer, promoting `Recovery center` as the primary entry point for action.
- Diagnostics copy, card hierarchy, and section density are now tuned for mixed-skill operators: summary cards jump to the relevant section, key cards lead with playback impact before raw telemetry, and direct shortcuts open the relevant configuration surfaces for devices, Bluetooth, Music Assistant, and latency.

## [2.42.0-rc.22] - 2026-03-20

### Fixed
- LXC one-click updates now keep the backend lockout active for the full apply/restart/startup cycle instead of briefly returning to the normal dashboard before the restart begins.
- After the updated bridge comes back on the new version, the web UI now performs a cache-busting page refresh so the browser reloads the latest HTML, JavaScript, and CSS immediately.

## [2.42.0-rc.21] - 2026-03-20

### Fixed
- Disabling a device from the dashboard now also updates the `Configuration → Devices` enabled toggle immediately, so `Save and restart` keeps the device disabled without requiring a page refresh first.
- The `All devices disabled` state now opens onboarding by default again and replaces the generic “Attach your first speaker” copy with guidance for re-enabling a configured device from `Configuration → Devices`.
- The onboarding `Review latency tuning` step now jumps to `Configuration → General`, highlights `PULSE_LATENCY_MSEC`, and focuses the correct field instead of sending operators to device settings.

## [2.42.0-rc.20] - 2026-03-20

### Changed
- Startup lockout copy is now clearer during the final startup grace period: `Finalizing startup` is shown as `Startup 90%`, and the follow-up message uses `Finalizing Startup` instead of `Startup complete`.

### Fixed
- Runtime status snapshots now include each device's global `enabled` flag, so disabling a live device no longer collapses into a plain `Released` state on the next status refresh.
- Disabled cards now keep their disabled status/sink labels and grayscale treatment instead of reverting after the runtime client is torn down.

## [2.42.0-rc.19] - 2026-03-20

### Changed
- The onboarding checklist is now toggleable from the header status badge in every guidance mode, while still opening by default only when no bridge devices are configured.
- Even healthy bridges keep the onboarding checklist available as an on-demand reference instead of dropping it entirely from the guidance payload.

### Fixed
- Completed onboarding steps once again render a visible checkmark inside their success indicator instead of showing only a green circle.

## [2.42.0-rc.18] - 2026-03-20

### Changed
- The onboarding checklist now uses clearer step circles with visible checkmarks for completed steps and ordinal numbers for the remaining steps.
- The header setup/status pill now opens the onboarding checklist directly, so operators can jump into pending setup work from the compact header state.

### Fixed
- Disabled device cards no longer lose their grayscale/inert state on the next live status refresh when `/api/status` omits `enabled` for active runtime devices.
- When configured devices exist but all of them are globally disabled, the dashboard now shows an explicit `All devices disabled` guidance state with a direct path to `Configuration → Devices`.

## [2.42.0-rc.17] - 2026-03-20

### Changed
- Disabled device cards and list rows now render in full grayscale, making the disabled state much more obvious across album art, icons, badges, and controls.
- Backend lockout artwork is now animated, with subtle motion during startup/restart and a gentler pulse for warning/unavailable states.

### Fixed
- HA add-on ingress refreshes no longer get stuck behind a frontend-only `Restoring bridge state` lockout after backend startup has already settled.

## [2.42.0-rc.16] - 2026-03-20

### Fixed
- Restart/startup lockout now stays active for the full live startup path, including single-device status payloads, so the dashboard no longer drops back to the normal UI while startup is still running or during `Finalizing startup`.

## [2.42.0-rc.15] - 2026-03-20

### Fixed
- Backend restart lockout now clears based on the live `Finalizing startup` phase instead of a generic frontend delay, so a normal page refresh no longer looks artificially locked while restart flows still stay protected until startup really settles.
- Devices become immediately inactive after `Disable`: their cards/rows stop reacting to clicks, sliders, transport controls, and settings actions as soon as the operator disables them.
- The Devices Bluetooth scan cooldown is now 10 seconds instead of 30, so operators can retry discovery much sooner.

## [2.42.0-rc.14] - 2026-03-20

### Fixed
- Backend restart/unavailable lockout now stays active for five extra seconds after status would normally clear it, giving the dashboard a short settle time before the full UI becomes interactive again.

## [2.42.0-rc.13] - 2026-03-20

### Fixed
- Restart/runtime lockout now also overrides the onboarding empty-state path, so the main UI is hidden correctly during restart even when the bridge is still in first-run onboarding mode.

## [2.42.0-rc.12] - 2026-03-20

### Fixed
- `More actions` dropdowns used by onboarding guidance, top-level banners, and diagnostics recovery actions now close when the operator clicks elsewhere on the page or presses `Escape`, matching normal menu behavior.

## [2.42.0-rc.11] - 2026-03-20

### Fixed
- Restart progress in the header now follows live backend startup/runtime state instead of a frontend-only scripted sequence, so `Restart complete` is shown only after the bridge is actually usable again.
- While restart/backend lockout is active, the page now keeps a centered runtime-status card in the main content area instead of leaving the body visually empty.

## [2.42.0-rc.10] - 2026-03-20

### Fixed
- Restart and backend-unavailable states now use a true top-level runtime lockout: the dashboard short-circuits normal rendering, clears stale device state, and hides everything except the header while the bridge is still starting or restoring.
- Runtime restore states no longer reuse misleading empty/setup copy such as `Waiting for setup`; the header now reports bridge startup/restoring state explicitly instead.

## [2.42.0-rc.9] - 2026-03-20

### Fixed
- During backend restart or temporary unavailability, the dashboard now hides stale onboarding/recovery content and locks the main UI so only the header plus the backend status banner remain visible until a usable status payload returns.
- Recovery/problem banners are now delayed briefly after startup completes, preventing noisy false alarms while adapters, Bluetooth links, and per-device startup tasks are still settling.

## [2.42.0-rc.8] - 2026-03-20

### Fixed
- HA ingress setups with zero configured bridge devices no longer show a false `Bridge backend is unavailable` banner just because the status payload still carries the legacy `No clients` marker; onboarding/setup guidance stays visible instead of being replaced by a backend-outage warning.
- Onboarding no longer duplicates its primary CTA in the top-right banner actions, keeping step-specific actions inside the expanded checklist cards where the operator is already working.

### Changed
- The Bluetooth `Adapters` configuration card now explicitly explains that it expects local controllers visible inside the bridge runtime, not MAC addresses of remote ESPHome Bluetooth Proxy nodes.
- When onboarding sends the operator into Bluetooth discovery, the `Already paired` section is now loaded and forced open as well, so existing paired speakers are visible immediately alongside the active scan flow.

## [2.42.0-rc.7] - 2026-03-20

### Added
- Empty-state onboarding is now action-oriented instead of read-only: unfinished checklist steps expand into concrete runtime details, targeted guidance, and per-step recommended actions that take operators directly to the relevant setup flow.

### Changed
- Adapter-present but no-device installs now stay in the onboarding empty/setup state, so the dashboard shows `Add first speaker` guidance instead of falling back to the generic waiting screen while setup is still incomplete.
- Recovery Center issue actions, top-level guidance banners, and backend-unavailable placeholders now share a more explicit operator UX model, reducing false empty-state messaging during backend restarts and keeping the same action language across the dashboard.

## [2.42.0-rc.6] - 2026-03-20

### Fixed
- Bluetooth release is now available even while a reconnect is in progress: releasing a speaker safely cancels the in-flight reconnect attempt before stopping the daemon and disconnecting Bluetooth, so operators can intentionally stop recovery without racing the background reconnect loop.
- User-released speakers are now treated as an intentional neutral state instead of a recovery problem, while auto-released speakers remain actionable attention items; the top-level guidance banner also keeps secondary recovery actions behind a compact `More actions` menu.

## [2.42.0-rc.5] - 2026-03-20

### Fixed
- Bluetooth recovery guidance now distinguishes “disconnected but still pairable” from “no longer paired”: reconnecting/unpaired devices recommend re-pair instead of reconnect, and the top-level recovery banner now includes reconnect attempt counts plus remaining attempts before auto-release when a threshold is configured.
- Auto-released devices are now labeled consistently as `Auto-released` in the UI, and release persistence is kept separate from global `enabled=false`, so BT-released devices no longer come back after restart as globally disabled devices.

## [2.42.0-rc.4] - 2026-03-20

### Added
- Added a unified operator-guidance contract and `/api/operator/guidance` endpoint, and embedded the same guidance payload into `/api/status`, SSE status updates, `/api/diagnostics`, and bugreport exports so the dashboard, diagnostics, and support flows all speak the same top-level guidance language.

### Changed
- Phase 2.1 is now live in the web UI: the large onboarding checklist only stays visible in the true empty state, non-empty installs surface setup/recovery progress through header status plus one primary attention banner, repeated issue groups now offer bulk reconnect/reclaim actions, and both onboarding/recovery guidance can be dismissed and restored from General settings without touching `config.json`.

## [2.42.0-rc.3] - 2026-03-20

### Added
- Added a recovery assistant contract and a new `/api/recovery/assistant` surface that group active issues by severity, recommended action, recovery traces, latency guidance, and a known-good test path derived from live bridge state.
- The web UI now shows a live recovery banner and a dedicated diagnostics recovery center with safe rerun actions, per-device recovery traces, latency-assistant hints, and guided “known-good” checks for isolating routing versus Music Assistant problems.

### Changed
- `/api/diagnostics` and bugreport full-text exports now embed recovery-assistant data alongside onboarding and device health, so downloaded reports start with actionable issue summaries instead of only raw status tables.
- Phase 2’s recovery UX is now additive and snapshot-driven: the frontend consumes explicit backend recovery data rather than inferring recovery guidance from scattered flags and event fragments.

## [2.42.0-rc.2] - 2026-03-20

### Added
- Device status payloads now include an explicit capability model grouped by operator-facing domains, with `supported`, `currently_available`, `blocked_reason`, and `safe_actions` for key bridge controls.

### Changed
- Core playback and recovery controls in the web UI now prefer backend-derived capabilities over ad-hoc frontend guesses, so reconnect, release/reclaim, play/pause, volume, mute, and queue gating explain themselves more consistently.
- Diagnostics device entries now include capability data alongside health summaries and recent events, so support flows can reason about “what is possible right now” instead of only current raw state.

## [2.42.0-rc.1] - 2026-03-20

### Added
- The web UI now shows a persistent onboarding checklist card with ordered setup steps, live progress, success checkpoints, and direct links into the relevant Bluetooth, device, Music Assistant, and diagnostics surfaces.

### Changed
- `/api/onboarding/assistant` now exposes a richer checklist-oriented payload, so onboarding and diagnostics can explain the current blocker, the next best action, and which first-playback milestones have already been reached.
- Operator setup guidance now follows the first Phase 2 UX model: setup is framed as an explicit “finish these steps” flow instead of leaving operators to infer readiness from scattered status widgets alone.

## [2.41.0-rc.2] - 2026-03-20

### Changed
- ROADMAP Phase 1 integration cleanup is now complete on `main`: route modules read runtime state through dedicated bridge/MA/job/adapter services, while `state.py` remains as a compatibility facade instead of the practical ownership center.
- Bridge lifecycle contracts are now locked down more explicitly with startup/shutdown integration coverage and README-level operator documentation for lifecycle events, diagnostics/telemetry surfaces, IPC protocol guarantees, and runtime hook behavior.

### Fixed
- Adapter-name caching now follows the active `config.CONFIG_FILE` path at load time and avoids repeated disk reads when the configured adapter-name set is legitimately empty.

## [2.41.0-rc.1] - 2026-03-20

### Added
- New runtime telemetry and event hook surfaces: `/api/bridge/telemetry` exposes bridge/subprocess resource data, and `/api/hooks` lets operators register runtime-scoped webhooks with delivery history for internal bridge/device events.
- Device event normalization now captures recent Bluetooth/runtime/MA transitions more consistently, so diagnostics and health summaries can explain degraded and recovering devices from recent event history instead of only current flags.

### Changed
- ROADMAP Phase 1 and Phase 2 runtime foundation work is now live on `main`: route read paths are snapshot-first, device inventory is owned by the canonical `DeviceRegistry`, startup/shutdown publication is tightened around `BridgeOrchestrator`, and parent/daemon communication now uses explicit IPC envelopes.
- Config lifecycle handling is now schema-aware end-to-end across load/save/import/export/Home Assistant translation paths, with shared migration/write helpers and safer preservation of persisted MA credentials plus runtime state.
- Diagnostics, onboarding, and status-adjacent APIs now reuse normalized snapshot/telemetry surfaces more consistently instead of mixing direct raw-state reads with duplicated enrichment logic.

### Fixed
- `/api/diagnostics` no longer re-runs expensive environment/subprocess collection when embedding telemetry, reducing duplicate `ps`/subprocess probing on lower-power systems.
- Bug reports now redact persisted OAuth tokens and runtime-state fields using the shared sensitive-key policy, preventing newly added config secrets from leaking into generated reports.
- Runtime hook registration now rejects loopback/private/link-local targets and invalid non-numeric timeout payloads, closing SSRF-prone and 500-shaped failure paths.
- Persisted `LAST_SINKS` entries now normalize MAC keys consistently during write/load pruning, so cached Bluetooth sink mappings no longer disappear because of lowercase or whitespace-padded MAC keys.
- Device-event helper annotations now accept canonical `DeviceEventType` values directly, aligning typing with the runtime call sites used by Bluetooth and Music Assistant event publishers.

## [2.40.6-rc.7] - 2026-03-19

### Fixed
- Music Assistant beta queue mode controls now work again for solo bridge players: `shuffle` / `repeat` treat MA `error_code` replies as real rejections and fall back from modern solo player ids to legacy `up...` queue ids when that is the actual queue target.
- Standalone Configuration now uses a shorter `Web UI port` helper so the port description fits on one line without wrapping.

## [2.40.6-rc.6] - 2026-03-19

### Fixed
- Existing LXC installs can once again update onto the new prerelease tag-based channel flow: runtime update checking no longer imports `scripts.release_notes`, and the LXC install/upgrade snapshot sync now copies the `scripts/` directory so staged validations keep matching the real application tree.

## [2.40.6-rc.5] - 2026-03-19

### Changed
- Release engineering now treats GitHub Releases as a stable-only surface: prerelease update discovery switches to Git tags plus the tagged `CHANGELOG.md`, and Home Assistant add-on variant sync now runs directly on every stable/RC/beta tag push without depending on the manual GitHub release workflow.

### Fixed
- Music Assistant beta transport skip controls now prefer player-level `next` / `previous` commands for normal player IDs while keeping the legacy queue fallback, so solo-player skip actions work again against newer MA beta builds.
- Home Assistant add-on polish: the ingress port field is now clearly read-only/shaded, its helper copy is shorter, and clicking the signed-in username opens the profile in a normal new browser tab instead of a popup-style window.

## [2.40.6-rc.4] - 2026-03-19

### Changed
- High-frequency bridge control routes and long-running Music Assistant/update actions now avoid blocking request threads: MA discovery/rediscovery, update checks, and queue commands use async job polling or optimistic completion flows instead of waiting synchronously in the Flask request path.
- Home Assistant add-on update track selection is now tied to the installed add-on slug, so the add-on options no longer expose `update_channel` switching and the bridge UI treats track/update guidance as read-only information.
- Home Assistant add-on mode now treats the web UI port as a fixed ingress property of the installed track and shows that port as read-only in Configuration, while leaving `base_listen_port` configurable for Sendspin player listeners.

### Fixed
- Password and backend log-level settings no longer report success when config persistence fails; runtime log-level propagation is only attempted after the config write succeeds.
- Login rate-limiting behind trusted Home Assistant ingress proxies now uses validated forwarded client identity instead of collapsing all users into the proxy IP bucket.
- Home Assistant add-on sessions now hide the logout button and route Music Assistant profile/group-settings links through add-on ingress instead of direct host/IP URLs.

## [2.40.6-rc.3] - 2026-03-19

### Changed
- The local demo now defaults to a more realistic signed-in header state, showing a user/logout block plus a Music Assistant token notice so preview screenshots better reflect the intended top-bar layout and onboarding guidance.

### Fixed
- Hidden notice cards now stay truly hidden even when the shared notice layout applies `display: grid`, preventing duplicate Music Assistant notices from appearing in demo.
- The header utility area now includes a visible divider between the theme toggle and the user/logout controls, so the top-right actions read as distinct groups again.
- The update-available badge no longer reuses RC/beta channel tinting; prerelease text coloring remains on the current-version badge only.

## [2.40.6-rc.2] - 2026-03-19

### Changed
- Top-of-page warnings now use a shared stacked notice-card layout with consistent icon/title/body/CTA structure, so security and Music Assistant notices match the rest of the dashboard card system and stack cleanly on mobile.

### Fixed
- The Music Assistant warning notice no longer appears when the runtime bridge integration is already connected, even if the saved-token validation probe disagrees.
- Header action links in the top-right corner once again keep visible spacing between their icons and labels.
- The theme switcher's `Auto` icon now renders as a visible circled `A` instead of collapsing into a filled circle in the header button.

## [2.40.6-rc.1] - 2026-03-19

### Added
- Home Assistant add-on ingress sessions can now try to obtain a long-lived Music Assistant token automatically when the UI opens, with a default-enabled opt-out toggle in Configuration → Music Assistant.
- The web UI now shows a warning banner when Music Assistant is discoverable but the bridge integration is still missing or using an invalid token, with a shortcut into the Music Assistant configuration section.

### Changed
- The theme switcher now has an explicit three-mode cycle (`Auto`, `Light`, `Dark`) instead of only manual light/dark toggling, and both the login page and the main dashboard now bootstrap the same saved theme mode consistently.

## [2.40.5-rc.3] - 2026-03-18

### Fixed
- Home Assistant add-on config validation no longer treats optional manual `web_port` / `base_listen_port` overrides as required fields, because unset values are now omitted from addon defaults and Supervisor option sync payloads instead of being sent as `null`.

## [2.40.5-rc.2] - 2026-03-18

### Added
- Bridge config, web UI, and Home Assistant addon options now support manual top-level `WEB_PORT` and `BASE_LISTEN_PORT` overrides. In Home Assistant addon mode, `WEB_PORT` opens an additional direct host-network listener while the fixed ingress endpoint keeps using the channel default port.

### Changed
- Home Assistant prerelease addon variants now combine distinct default ingress/player port ranges, manual startup defaults, channel-specific branding, and HA-safe prerelease notices so parallel stable/RC/beta installs are easier to distinguish and safer to run on one HAOS host.
- The GitHub release workflow now builds the release body from the matching `CHANGELOG.md` section and uses GitHub-generated notes only as an optional supplement, preventing empty autogenerated releases.

### Fixed
- Music Assistant album artwork now loads correctly through Home Assistant ingress because artwork proxy URLs stay relative to the active addon origin instead of escaping to the Home Assistant root.
- Solo-player Music Assistant transport controls now keep working when Music Assistant syncgroup discovery is empty because queue commands respect an explicit solo queue ID instead of requiring `ma_groups` to be populated first.
- Header version/update indicators now tint only the RC/Beta version text instead of coloring the entire badge, and Home Assistant add-on info/docs now render prerelease notices correctly through HA-safe badge markdown.

## [2.40.5-rc.1] - 2026-03-18

### Fixed
- Solo-player Music Assistant transport controls now keep working on live Proxmox/LXC deployments even when MA syncgroup discovery is empty, because queue commands now respect an explicit solo queue ID instead of requiring `ma_groups` to be populated first

### Changed
- Header version badges and discovered-update badges now highlight prerelease channels directly in the UI: RC builds use yellow styling and beta builds use red styling
