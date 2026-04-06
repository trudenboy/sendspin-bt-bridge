---
title: 2026-03-31 — Stats dashboard, Russian landing, and v3 roadmap
description: March 31 delivered a stats dashboard with live GitHub data, a static Russian landing page, SEO and analytics integration, the v3.0+ roadmap, beta channel CI, and v2.52.1 with LXC upgrade fixes
---

The day after the landing page launch shifted focus from feature development to project visibility and long-term planning. A statistics dashboard, Russian landing page, analytics integration, and a comprehensive v3 roadmap all landed in a single day — alongside the beta branch CI pipeline and v2.52.1 with LXC upgrade fixes.

## What shipped

### Stats dashboard for the docs site

The Astro Starlight documentation site gained a full project statistics page with live GitHub API data:

- **SVG area chart** for traffic trends (views, clones) — replaced a simpler bar-row design through three iterations as CSS variables didn't render in SVG contexts; landed on explicit inline fill/stroke attributes
- **Stacked bar chart** for release downloads — replaced the static releases table; scrapes real GHCR container pull counts per release via the packages API
- **Development activity section** — code velocity (additions/deletions over time), AI collaboration stats (Copilot vs Claude Code co-authored commits), and language breakdown
- **Issues & PRs section** — open/closed ratios, average time to close, most active contributors
- **CI workflow section** — success rate, average duration, recent runs
- **Grouped indicator cards** — redesigned from individual badges to flat rectangular tiles after the grouped cards felt cluttered; includes Container pulls, GitHub stars, forks, and CI status

A notable technical challenge was GitHub's Stats API returning 202 ("computing") on first access. The dashboard now pre-warms the API 15 seconds before rendering and retries 5×4 s for `code_frequency` endpoints.

### Russian landing page

Rather than relying on Google Translate's runtime widget (which flickers and reformats), a static Russian translation was created via the DeepL API and served as a standalone HTML page:

- Full static translation with manual quality pass for technical terms ("мультирум" vs "многокомнатный", "колонка" vs "динамик")
- Language switcher links to static pages instead of triggering runtime translation
- Mobile heading overflow on 375 px fixed with `<wbr>` hints and reduced font size
- Hero glow effect contained with overflow clipping to prevent horizontal scroll

### SEO and analytics

Comprehensive SEO optimization across both landing pages (EN + RU):

- **Structured data**: FAQ Schema (`FAQPage` JSON-LD), Product markup for the bridge
- **Social previews**: optimized OG images (compressed from ~200 KB to ~50 KB), shortened descriptions for Twitter/Telegram/Facebook card limits
- **Analytics**: Google Analytics 4, Yandex Metrica, and Microsoft Clarity all integrated with cookie-free tracking
- **Search Console**: verification meta tags for Google and Yandex; `robots.txt` and sitemap added to docs-site
- **Navigation polish**: scroll-margin-top for fixed nav offset, deployment cards linked to documentation pages

### v3.0+ roadmap (PR #118)

A comprehensive architecture roadmap was published as `ROADMAP_V3.md`, planning the evolution from `sendspin-bt-bridge` to `sendspin-audio-bridge`:

- **Track A**: Backend abstraction layer — unified `AudioBackend` interface for Bluetooth, local audio (PA/PW/ALSA/USB), network (Snapcast, VBAN), and virtual sinks
- **Track B**: Multi-bridge federation — discover and coordinate multiple bridge instances via MA API
- **Track C**: HA/AI automation — HA-native device entities, presence-based routing, TTS interleaving
- **Track D**: Management CLI — `sendspin-cli` for headless fleet management
- Integration model: HA addon + `hass_players` (not upstream MA provider)

### Beta channel CI

CI was extended to support the `beta` branch for v3.0 development:

- `release.yml` now detects the `beta` branch and creates pre-release tags (`v3.0.0-beta.N`)
- HA addon `ha-addon-beta/` config synced automatically on beta pushes
- First 14 beta releases shipped on March 31 (v3.0.0-beta.1 through beta.14), bringing the Vue 3 frontend to full feature parity with the legacy UI — 108 features across 30 categories, 489 Vue tests

### v2.52.1

A maintenance release fixing LXC upgrade issues discovered after the `.gitattributes` export-ignore optimization:

- `demo/` directory removed from `upgrade.sh` sync list — it was excluded from the release tarball but the script still tried to sync it
- Directory existence guards added to prevent failures when optional dirs are missing
- `install.sh` updated to match

## Why this matters

The stats dashboard and Russian landing page are community-building investments. The project now has real-time visibility into adoption metrics (container pulls, traffic, star velocity) and is accessible to Russian-speaking HA communities without relying on machine translation artifacts.

The v3 roadmap establishes the architectural direction beyond Bluetooth — local audio, network backends, and multi-bridge federation. Publishing it openly invites community feedback before implementation begins.

The beta CI pipeline is infrastructure for the Vue 3 frontend migration. Having automated releases on a separate branch means beta testers can opt in to the new UI without risking stable installations.

## Follow-up

The stats dashboard revealed that GitHub's Stats API is unreliable for first-time access (202 responses). Future improvements could cache the data in a GitHub Actions workflow and serve static JSON.
