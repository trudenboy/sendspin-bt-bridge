---
title: 2026-03-28 — Mobile UI overhaul, diagnostics redesign, and v2.51.0
description: March 27–28 delivered an idle standby timer fix, complete mobile portrait redesign, in-container log viewer, artwork proxy fix, diagnostics UX overhaul, and mDNS long name handling across v2.50.4 through v2.51.0
---

Two days of intense UI work reshaped how operators interact with the bridge on phones and when troubleshooting. The mobile overhaul made the web interface genuinely usable on portrait screens, while the diagnostics redesign turned a wall-of-text panel into a navigable tool with simple/advanced modes.

## What shipped

### Idle standby timer fix (v2.50.4)

The standby timer had a subtle bug: it only started counting when audio stopped playing, but never started when a speaker connected and *never* played anything. This meant speakers that connected during a quiet period would stay connected indefinitely. The fix starts the timer both on audio stop and on initial daemon connection when no audio is active.

### Mobile UI overhaul (v2.51.0-rc.1)

The web interface was redesigned for phone screens:

- Grid view became the default in portrait; list view is landscape-only
- Volume slider, mute, and pause share a single compact row (104px vs 321px)
- All action buttons (Reconnect, Standby, Disable) fit one row on standard phone widths
- Hamburger menu and bottom navigation added for mobile
- Safe-area inset support for notched devices (iPhone, modern Android)
- Touch targets enlarged to 44px minimum
- Horizontal scroll eliminated across all breakpoints
- Dark theme contrast improvements

### In-container log viewer (#111)

The log viewer previously shelled out to `docker logs`, which is unavailable inside the container itself. A 2000-line in-memory ring buffer now serves logs directly, with severity-based filtering and clipboard copy.

### Artwork proxy fix (#112)

Album art URLs containing Unicode characters (common with non-Latin track names) were failing because the MA `/imageproxy` URL wasn't being URL-encoded. The fix properly constructs imageproxy URLs and encodes all path components, so artwork loads correctly for tracks in any language.

### Diagnostics UX overhaul

The diagnostics panel was rebuilt from scratch:

- **Simple/Advanced toggle** — Simple mode hides 76% of the content, showing only active issues and a verification path. Advanced mode reveals everything. The preference persists in localStorage.
- **Sticky navigation strip** — jump between sections (Issues, Health, Speakers, Routing, MA, Advanced) with auto-highlighting on scroll
- **Health status pills** — colored at-a-glance badges for speakers, sinks, MA, and adapters
- **Collapsible Recovery center** — five sub-sections with count badges, collapsed by default
- **Speaker filter** — search input for setups with many speakers
- **Copy for support** — clipboard button copies diagnostics summary or per-device info
- **Contextual bug reports** — bug button on issue cards pre-fills the report with device and issue context
- **Humanized timestamps** — "3 min ago" instead of ISO-8601; hover reveals full datetime
- **Sticky action footer** — Refresh, Download, Copy, Report always visible

### Long speaker name fix (#115)

Names like `[AV] Samsung Soundbar M360 M-Series @ asus-laptop-ubuntu` exceeded the mDNS 63-byte label limit, causing `zeroconf.BadTypeInNameException` crashes. The fix truncates names to fit and switches fallback player IDs to UUID5 — deterministic, always 36 chars, no mDNS issues.

## Why this matters

The mobile overhaul addressed the #1 UX complaint: operators manage their speakers from phones, but the UI was designed for desktop widths. Grid layout, compact action bars, and proper touch targets made it genuinely usable on a phone held in one hand.

The diagnostics redesign was equally important for a different reason: the bridge runs headlessly and operators need to troubleshoot without SSH access. The simple/advanced toggle means casual users see only what matters, while power users can drill into every subsystem.

The long name fix (#115) was a production crash discovered by a community member running a Samsung soundbar — exactly the kind of device the bridge targets. UUID5-based player IDs are a better long-term foundation than sanitized strings.
