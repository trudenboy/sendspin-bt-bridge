# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
