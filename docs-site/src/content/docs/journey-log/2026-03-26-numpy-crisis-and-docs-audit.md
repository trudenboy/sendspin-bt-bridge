---
title: 2026-03-26 — NumPy CPU crash, documentation audit, and v2.50.0
description: March 26 brought a critical NumPy X86_V2 compatibility crash, three rapid-fire hotfix releases, a comprehensive documentation audit with fresh screenshots, and the v2.50.0 stable release
---

A quiet morning of documentation work turned into an emergency hotfix marathon when users on older CPUs started reporting daemon subprocess crashes. The root cause was NumPy ≥2.0 requiring the X86_V2 instruction set (SSE4.2, POPCNT) — absent on Celeron, Pentium, and early Core i3 processors. Three hotfix releases landed within hours.

## What shipped

### NumPy X86_V2 compatibility crisis (#109)

The crash manifested as an `ImportError` deep inside NumPy on startup. The fix was straightforward — pin `numpy<2.0` — but the rollout required three attempts:

- **v2.49.1** — initial pin in `requirements.txt`, but the `sendspin` package itself pulled in NumPy ≥2.0 transitively
- **v2.50.1** — added `numpy<2.0` as an explicit constraint, but pip's resolver didn't always honor it during `sendspin` install
- **v2.50.2** — verified the pin worked end-to-end in a clean container build
- **v2.50.3** — enforced `numpy<2.0` constraint during the `sendspin` install step itself via `pip install "numpy<2.0" && pip install sendspin`

The lesson: transitive dependency constraints need to be enforced at install time, not just declared. A `requirements.txt` pin means nothing if a downstream package's `install_requires` pulls in the newer version during its own dependency resolution.

### Comprehensive documentation audit

Before the NumPy crisis hit, the day started with a full documentation audit:

- Rewrote and aligned all docs pages with the current v2.50.0 architecture
- Added Standby & Wake-on-play section with auto-off guidance and deep sleep tooltip
- Retook 8 outdated screenshots from the live demo to match the current UI
- Renamed `.md` → `.mdx` for files using Starlight components
- Moved HISTORY.md chronological entries into the Journey log

### v2.49.0 stable and v2.50.0 stable

Two stable releases shipped on the same day:

- **v2.49.0** — the culmination of 27 RC releases: null-sink standby with auto-wake, standby as a first-class status, configuration UX overhaul, unified CI/CD pipeline, and 125+ new tests
- **v2.50.0** — dependency modernization (websockets 13→16, waitress 2→3) with the NumPy fix chain

### Dependency bumps (v2.50.0-rc.1)

Core runtime and CI dependencies were updated:

- `websockets` 13.1 → 16.0 — migrated to `websockets.asyncio.client` API
- `waitress` 2.1.2 → 3.0.2
- `pytest-asyncio` updated
- CI actions: checkout v6, setup-python v6, deploy-pages v5, setup-buildx v4, upload-artifact v7, setup-node v6, github-script v8

### Bluetooth adapters guide (#110)

A new documentation page with recommended Bluetooth adapters was added in both English and Russian, based on production experience with CSR8510 A10 adapters on HAOS and Proxmox passthrough.

## Why this matters

The NumPy crash was the first compatibility issue to affect a meaningful number of users — anyone on budget x86 hardware. The rapid hotfix cycle (three releases in one day) validated the unified release pipeline introduced in v2.49.0: edit VERSION, push, CI handles everything. What would have been a multi-hour manual process became a 2-minute turnaround per fix.

The documentation audit brought the docs in line with the massive UI and architecture changes from the preceding two weeks. Fresh screenshots and Starlight component usage made the docs site feel like a maintained product rather than an afterthought.
