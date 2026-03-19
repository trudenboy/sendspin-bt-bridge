---
title: 2026-03-19 — 2.40.6 release wave, UI polish, and roadmap realignment
description: March 19 covered the full 2.40.6 release wave — demo and docs refresh, theme and HA UI polish, MA beta compatibility fixes, release-engineering changes, and the final roadmap/README realignment
---

March 19 turned into a full release-and-docs day, not a narrow documentation pass.

By the end of the day, the project had moved through a broad `2.40.6` release wave: demo and screenshot-stand refreshes, theme and notice polish, Home Assistant add-on UX and semantics cleanup, Music Assistant beta compatibility fixes, prerelease delivery changes, and finally the roadmap/README realignment work that updated the project's planning story to match the runtime already in the repository.

## What changed

### The day started with a major docs and demo refresh

The first wave focused on the public-facing demo and docs surfaces:

- the screenshot stand and demo fixtures were refreshed
- docs pages and screenshots were updated in both English and Russian
- the demo dashboard defaults were corrected
- demo realism improved enough to support more representative screenshots and UI review

That early work mattered because the project increasingly uses the demo stack as a repeatable environment for docs, UI validation, and release presentation.

### Theme controls and top-of-page notices were polished

The next UI wave focused on the global shell of the application:

- the theme switcher gained a proper `Auto` mode
- the header, notices, and warning-card layout were cleaned up
- theme icon rendering and top-right spacing issues were fixed
- demo header state and notice behavior were aligned with the intended production UX

This was a visible quality-of-life improvement for both the real web UI and the demo surfaces used in docs and release screenshots.

### Home Assistant add-on semantics became much clearer

Several changes tightened the meaning of add-on mode:

- add-on sessions can try to auto-connect a Music Assistant token when the UI opens
- long-running MA and update flows moved toward async job polling or optimistic completion instead of blocking Flask request threads
- add-on mode now treats the installed track and ingress web port as fixed properties of the installed variant
- UI helpers, links, and warnings were adjusted around those fixed add-on realities

This is part product polish and part operational hardening: the add-on UI now explains more clearly what is configurable and what is defined by the installed HA track.

### Music Assistant beta transport compatibility was repaired

The release wave also contained real runtime fixes:

- queue and transport controls were repaired for newer MA beta behavior
- solo-player `shuffle` and `repeat` regained compatibility through legacy queue fallbacks where needed
- `next` / `previous` behavior was corrected for player-level vs queue-level control
- related tests in MA monitor, API endpoints, and demo flows were expanded

This was one of the most important engineering parts of the day because it restored behavior on real MA beta installations instead of only polishing presentation layers.

### Release engineering shifted toward tag-based prerelease flow

March 19 also reshaped how prereleases move through the project:

- prerelease update discovery was routed through Git tags plus tagged changelog content
- GitHub Releases became effectively a stable-only surface
- HA add-on variant sync was split into its own workflow
- add-on changelogs were filtered by channel
- LXC prerelease updater staging was fixed so existing installs could follow the new flow correctly

This gave the project a more explicit separation between stable release storytelling and prerelease delivery mechanics.

### The release wave culminated in `2.40.6`

The commit history for the day includes repeated RC preparation and add-on sync steps from `2.40.6-rc.1` through `2.40.6-rc.7`, followed by stable `2.40.6`.

The resulting stable release grouped the day’s work around three main outcomes:

- safer async MA/update request behavior
- clearer HA add-on track and ingress semantics
- repaired MA beta transport compatibility plus LXC prerelease update recovery

### The roadmap now starts from the live architecture

`ROADMAP.md` was rewritten so it no longer treats already-implemented runtime work as future ambition.

The updated roadmap now recognizes that the codebase already includes:

- `BridgeOrchestrator`
- startup progress and runtime metadata publication
- snapshot/read-side status models
- protocol-versioned IPC helpers
- onboarding assistant guidance
- baseline config validation

That changes the planning posture substantially. The next phases are now framed around **finishing** the current v2 runtime foundation, strengthening contracts and diagnostics, and only then moving toward backend abstraction for v3.

### README messaging was brought back into sync

The top-level `README.md` and `README.ru.md` now summarize the same direction as the new roadmap:

- complete the runtime foundation already in progress
- reduce `state.py` coupling
- formalize contracts, diagnostics, and config lifecycle safety
- improve onboarding and recovery UX
- treat backend abstraction as a later step, not as the next rewrite

This matters because the README is where most users and contributors form their first mental model of the project.

### A short Russian roadmap now exists

`ROADMAP.ru.md` was added as a concise Russian summary for the current roadmap.

The English `ROADMAP.md` remains the source of truth, but the Russian-facing surfaces now have a clearer bridge between:

- the full roadmap
- the Russian README
- the docs site's Russian section

### README landing pages were simplified after the release wave

Late in the day, the README surfaces were simplified and refocused:

- landing pages became shorter and more scannable
- quick-start framing was tightened
- capabilities and prerequisites were refreshed
- roadmap messaging was brought into line with the new planning baseline

This made the repository entry points better match what the project now actually is: a mature Bluetooth-first bridge with a stronger runtime architecture than the older docs implied.

### The roadmap/docs changes were shipped through an existing PR flow

The documentation refresh was committed, pushed, and merged through the already-open branch/PR path, so the roadmap and README updates landed on `main` the same day they were prepared.

## Why this matters

March 19 matters because it tied together release engineering, runtime compatibility, UX polish, demo/docs hygiene, and project planning in one coherent release wave:

- the `2.40.6` line now captures real HA add-on, MA beta, and updater behavior fixes
- the UI shell and demo surface became cleaner and more representative
- release mechanics became clearer for stable vs prerelease channels
- contributors no longer have to guess which architecture work is still pending and which is already live
- the path to v3 is now framed as a continuation of the current runtime instead of a restart

## Follow-up

This entry becomes the handoff point for the next wave of work:

- keep future release notes tied closely to the tagged changelog and actual delivery flow
- carry the updated roadmap framing into more docs-site surfaces where useful
- keep architecture notes anchored to what the code already implements
- use the new roadmap as the planning baseline for runtime, diagnostics, onboarding, and v3 preparation
