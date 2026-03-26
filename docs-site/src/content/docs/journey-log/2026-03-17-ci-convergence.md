---
title: "March 17: CI convergence and release completion"
description: "Solo-player transport recovery, DaemonArgs compatibility, release discipline, and CI packaging hardening"
---

## March 17, 2026 — CI convergence and release-image runtime completion (v2.32.12)

The `2.32.12` release is a narrow but important follow-up to `2.32.11`. The previous release intentionally tightened CI and release validation, but the first live runs exposed two remaining gaps: one in linting consistency between local hooks and GitHub Actions, and one in the actual dependency payload of release images built against a pinned `sendspin` version. This release closes both gaps so the validation pipeline is not only stricter, but also internally consistent.

Two things define the release:

- **Linting now agrees on what the codebase should accept** — the repository no longer depends on a source-level `noqa` that means different things to different Ruff entrypoints. Instead, the local `pre-commit` hook explicitly relaxes `UP038`, which preserves runtime-safe `isinstance(..., (list, tuple))` syntax for local Python 3.9 environments while keeping CI’s own `ruff check` clean and unsurprising.
- **Release images now contain the runtime they claim to validate** — the non-`armv7` Docker release path no longer installs pinned `sendspin` with `--no-deps`. That means smoke-tested images now actually include `aiosendspin`, `av`, and the rest of the package graph that `sendspin` needs at runtime, instead of passing the build and then failing only when the built container is exercised.

This is exactly the kind of release that should be small: it does not add a new feature, it removes the remaining mismatch between local expectations, CI validation, and the contents of the published runtime image.

---

## March 17, 2026 — CI packaging follow-up and default list-first dashboard (v2.32.11)

The `2.32.11` release is a compact follow-up to `2.32.10`, but it closes a very practical gap between “the code is ready” and “the release machinery is actually trustworthy.” After the previous release was pushed, GitHub Actions exposed a few environmental assumptions that were true locally but not on the hosted runners. This release tightens those assumptions so the pipeline behaves the same way in CI as it does in real deployment environments.

Three adjustments define the release:

- **The dashboard now opens in the layout that works best as a default operational view** — new sessions start in `list view`, which is denser, easier to scan, and better aligned with the current dashboard’s monitoring role. At the same time, this does not override user intent: any previously saved choice in `localStorage` still wins.
- **Release preparation is now explicit about native D-Bus build requirements** — the Docker publish workflow’s version-resolution path now installs the D-Bus development packages required by `dbus-python` before reading the packaged `sendspin` version. That removes a class of CI-only failures where the release pipeline broke before the actual image build had even started.
- **Compatibility smoke tests are now provisioned like the code they exercise** — the lint/test workflow installs the PortAudio runtime library before running the `sendspin` compatibility check, and the Dockerfile’s conditional dependency branch now uses a hadolint-compliant `elif` structure. Together these fixes turn the new release gates from “correct in principle” into “reliable in automation.”

This is not a feature-heavy release. It is a release about making the defaults and the delivery pipeline more honest: the UI starts in the most practical overview mode, and the CI system now has the native pieces it actually needs to validate what we ship.

---

## March 17, 2026 — Release discipline, operational visibility, and playback UX follow-ups (v2.32.10)

The `2.32.10` release ties together several threads that all reduce ambiguity in day-to-day operation. Some of the changes are under-the-hood release hardening, some are diagnostics improvements, and some are small but very visible dashboard fixes — but they all move in the same direction: make the bridge behave more predictably both for operators and for end users.

Four themes define the release:

- **Release creation is now an explicit operation rather than an accidental side effect of tagging** — GitHub releases are now handled by a dedicated manual workflow. It defaults to the latest tag, still allows choosing a specific tag, generates cumulative release notes from the previous published release, and updates `ha-addon/config.yaml` only when the release itself is created. Tag pushes no longer silently rewrite add-on metadata.
- **Dependency compatibility is visible and gated earlier** — the bridge now records resolved runtime dependency versions in startup logs, diagnostics, bugreports, and `/api/version`, while CI and Docker release paths include a real compatibility smoke-check against the installed `sendspin` runtime. This makes upstream drift easier to catch before publication and much easier to diagnose after deployment.
- **Issue severity is now closer to operational reality** — crash-like subprocess `stderr` is no longer flattened into an ordinary warning. The runtime logger, bugreport summary, diagnostics output, and `Report an Issue` affordance now share the same understanding of which log lines actually represent actionable failures.
- **Playback UI got another round of trust-building polish** — the dashboard filter bar now remains visible even for a single player, card-view `shuffle` / `repeat` buttons finally show their active state clearly, and `repeat one` now uses an integrated icon rather than an overlaid badge. These are small details, but they matter because transport controls need to communicate state instantly and unambiguously.

This is not a “one big feature” release. It is a release about removing quiet sources of confusion: confusion in release ownership, confusion in packaged dependency state, confusion in log severity, and confusion in transport UI feedback. That kind of cleanup tends to age very well.

---

## March 17, 2026 — HA add-on startup compatibility hotfix for `DaemonArgs` drift (v2.32.9)

The `2.32.9` release is a narrow but important follow-up hotfix to `2.32.8`. The bridge itself did not regress in its Music Assistant routing logic, but the Home Assistant add-on could fail before it even brought up any player subprocesses. The root cause was a packaging boundary problem: our daemon launcher still assumed that `sendspin.daemon.daemon.DaemonArgs` accepted `use_hardware_volume`, while the installed `sendspin` version in at least some HA add-on environments no longer exposed that keyword.

Three things matter in this release:

- **Startup compatibility over strict kwarg assumptions** — the daemon subprocess now builds its `DaemonArgs` payload defensively, filtering the startup kwargs against the signature actually provided by the installed `sendspin` package. If an older or newer `sendspin` build omits a field like `use_hardware_volume`, the bridge skips that kwarg instead of crashing during startup.
- **HA add-on recovery from immediate boot failure** — this is specifically a boot-path fix. It restores the add-on’s ability to start after the `2.32.8` update in environments where the packaged `sendspin` API surface drifted away from the bridge’s expectations.
- **Regression coverage for compatibility filtering** — the release adds tests around the kwarg-filtering behavior so that future Sendspin API drift is more likely to surface as a targeted test failure than as a production startup crash.

This is the kind of release that exists to reintroduce boring reliability: no UI changes, no new transport semantics, just a tighter compatibility boundary between the bridge and the packaged daemon API it depends on.

---

## March 17, 2026 — Solo-player MA transport recovery and calmer apply-state UX (v2.32.8)

The `2.32.8` release is a follow-up hotfix to the recent Music Assistant transport work, but it fixes a very real operational problem rather than polishing around the edges. The bridge was already fast enough at the MA monitor layer — queue commands were being acknowledged in a few milliseconds — yet transport controls on a live Proxmox deployment could still appear broken. The root issue was identity drift: the dashboard, the bridge cache, and Music Assistant were not always talking about the same queue object.

Three threads define the release:

- **Solo-player queue targeting instead of stale identity reuse** — the bridge now distinguishes between the local state key used for dashboard cache updates and the actual MA queue/player ID that should receive the command. That matters for solo universal-player bridges, where the UI-facing bridge player ID and the MA queue ID are not the same thing.
- **Stale-tab resilience on live deployments** — if an already-open dashboard page keeps sending outdated MA target metadata after a hotfix rollout, the backend can now recover by inferring the correct active solo player queue instead of blindly trusting the stale syncgroup hint. In practice, that means transport controls recover faster in the real world, not just after a hard browser refresh.
- **Quieter apply-state behavior** — queue buttons still lock while a command is pending, but the temporary “apply” state no longer adds extra visual highlighting. The controls now simply become inactive until the command settles, which makes both card and list playback surfaces feel calmer and less noisy during normal use.

This is the kind of release that looks small in a diff but large in effect: the MA monitor path was already fast, yet the operator experience still felt broken because commands were landing on the wrong target or because stale runtime metadata outlived a hotfix. `2.32.8` closes that gap by making queue routing more explicit, more backend-authoritative, and more tolerant of real live-deploy conditions.

---
