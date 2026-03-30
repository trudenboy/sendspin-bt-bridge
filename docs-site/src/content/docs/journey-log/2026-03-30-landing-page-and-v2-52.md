---
title: 2026-03-30 — Landing page, auto-translation, and v2.52.0
description: March 30 delivered an SMM-optimized landing page on Cloudflare Pages, Google Translate integration for landing and docs, LXC upgrade hardening, TDD guardrails, config schema, and the v2.52.0 stable release
---

The final day of the week shifted from runtime features to project presentation and developer experience. An SMM-optimized landing page went live on Cloudflare Pages with auto-translation, while code quality guardrails (TDD rules, CRITICAL markers, config schema) were added for AI agent workflows.

## What shipped

### SMM-optimized landing page

A single-file HTML landing page (~85KB, all CSS/JS inline) was built from scratch and deployed to [sendspin-bt-bridge.pages.dev](https://sendspin-bt-bridge.pages.dev/):

- Hero section: "Make Any Speaker Smart" with clear value proposition
- Six feature cards covering multi-room, protocols, HA automation, and AI
- Interactive infographic with the connection flow
- Ecosystem section with Sendspin, Music Assistant, and Home Assistant links
- Screenshot showcase with tab switching and scroll-position anchoring
- Share bars (top + sticky bottom) for X, Reddit, Telegram, LinkedIn, Facebook
- Wave Bridge logo: two rounded pillars with three sine-wave cables in teal→purple gradient
- Structured data (JSON-LD) and full Open Graph/Twitter Card metadata

### Google Translate integration

Both the landing page and the Astro Starlight documentation site gained auto-translation:

- Custom language picker dropdown with 14 popular languages (EN, ZH, ES, FR, DE, RU, UK, PT, JA, KO, AR, HI, IT, TR)
- "More languages…" option opens the native Google Translate widget for 100+ languages
- Hidden GT widget under the hood — the custom dropdown triggers translation via cookie (`googtrans=/en/{lang}`) and programmatic select change
- Dark-themed with blur, gradient accents, and animated chevron
- On the docs site, the picker hooks into Starlight's `astro:page-load` event for SPA navigation compatibility

### Cloudflare Pages deployment

The landing page was deployed to Cloudflare Pages for global CDN edge delivery. Screenshots are served from the same CDN. The GitHub Pages copy at `trudenboy.github.io/sendspin-bt-bridge/landing/` uses absolute GitHub URLs for screenshots since the directory structure differs.

### LXC upgrade hardening (v2.52.0-rc.3 through rc.5)

Three issues in `upgrade.sh` were fixed through rapid RC iterations:

- **Download-to-file**: replaced fragile `wget | tar` pipe with temp file download + retry. The pipe combined with `set -euo pipefail` caused silent failures on 404 or network interruption
- **stderr pollution**: `warn()` and `msg()` output was going to stdout, polluting the `SNAPSHOT_ROOT` path variable captured by command substitution
- **Self-update**: `upgrade.sh` now fetches its own latest version from the target release ref before running the full upgrade, preventing chicken-and-egg failures where the old script can't parse the new release format

### TDD rules and code quality guardrails (v2.52.0-rc.6)

Developer experience improvements aimed at AI agent workflows:

- TDD rules in CLAUDE.md and CONTRIBUTING.md: red/green/refactor cycle, five constraints (never modify tests to pass, no tautological tests, test real behavior, every test must be able to fail, reproduce bugs first)
- CRITICAL risk markers on 7 high-risk code zones: audio routing, thread safety, path traversal, auth bypass, config persistence, IPC protocol, subprocess lifecycle
- CI test protection: PR warning when test files change without corresponding source changes or bulk modifications
- `config.schema.json`: machine-readable JSON Schema for `config.json` covering all 40+ fields with device and adapter sub-schemas

### Test audit

Fixed tautological tests across 6 test files — tests that reimplemented the logic under test locally and asserted on that copy instead of calling the real function. Added missing assertions in `container_runtime`, `ma_runtime_state`, and `config` tests.

### UX polish

- Wake button got a distinct sunrise icon (previously shared the reconnect icon)
- Bug report dropdown: removed emoji icons for consistent UI style
- Bug report modal: defaults to "Open on GitHub", shows email field only for proxy submissions

### v2.52.0 stable

The stable release consolidated everything from v2.51.1-rc.1 through v2.52.0-rc.6: bug report proxy, Docker/AppArmor fixes, LXC upgrade hardening, landing page, auto-translation, TDD guardrails, and config schema. Test suite: 965 tests.

## Why this matters

The landing page is the project's first real marketing asset. Previously, discovery relied entirely on the HA Community forum post and the GitHub README. A dedicated landing page with structured data, social sharing, and auto-translation into 100+ languages dramatically expands discoverability — especially for non-English-speaking HA communities.

The TDD guardrails and CRITICAL markers are an investment in AI-assisted development quality. As the project increasingly uses AI agents for implementation, having explicit rules prevents the most common failure modes: tautological tests, mock-only testing, and changes to high-risk code without adequate review.

The LXC upgrade hardening closes a reliability gap that caused four consecutive failed upgrades on the Turris router deployment. Download-to-file with retry and self-update before full upgrade make the upgrade path robust against network flakiness and version format changes.

## Follow-up

With the landing page live and v2.52.0 stable, the next focus areas are community growth (HA forum engagement, Reddit/Telegram promotion) and runtime improvements for the v3 backend abstraction outlined in the roadmap.
