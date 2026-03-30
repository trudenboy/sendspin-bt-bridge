---
title: 2026-03-29 — Bug report proxy, Docker Compose fix, and v2.51.0 stable
description: March 29 shipped v2.51.0 stable with mobile UI and diagnostics, then added a GitHub App bug report proxy, Docker Compose Bluetooth fix for Ubuntu/Debian, and release asset download tracking
---

With v2.51.0 stable out the door, the focus shifted to lowering barriers for community participation and fixing a Docker Bluetooth issue that had been plaguing Ubuntu and Debian users.

## What shipped

### v2.51.0 stable

The stable release consolidated the mobile UI overhaul, diagnostics redesign, artwork proxy fix, in-container log viewer, and mDNS long name handling from five RC builds. The test suite stood at 964 tests.

### Docker Compose Bluetooth fix (#114)

Users running Docker Compose on Ubuntu and Debian hosts reported that Bluetooth failed to initialize even with `privileged: true`. The root cause: AppArmor and seccomp defaults were blocking the `AF_BLUETOOTH` socket family. The fix adds `security_opt: apparmor:unconfined, seccomp:unconfined` to `docker-compose.yml`, matching the permissions the HA addon already had.

The HA addon's own AppArmor profile also needed `dbus,` and `network raw,` rules for HA Supervised installations on Ubuntu 24.04+, where the default profile is stricter.

### Bug report proxy via GitHub App

Many bridge operators don't have GitHub accounts — they're home automation users, not developers. The bug report modal now offers three submission paths:

1. **Open on GitHub** — direct link for users with accounts
2. **Submit Report** — proxy submission via a GitHub App that creates issues on behalf of the user, authenticated with RS256 JWT signing
3. **Copy to Clipboard** — fallback for offline or restrictive environments

The proxy endpoint (`/api/bugreport/submit`) enforces rate limiting: 3 reports per IP per hour, 20 globally per day. Email is required for proxy submissions (for follow-up contact) but hidden when using clipboard mode.

New dependencies: `PyJWT>=2.8.0` and `cryptography>=3.4.0` for GitHub App JWT signing. Initially bundled as `PyJWT[crypto]`, but pip's constraint resolver couldn't handle the extras bracket in combination with other constraints — split into separate deps.

### Release asset download tracking

Stable releases now attach a source tarball as a release asset. The LXC `upgrade.sh` script prefers the tracked release asset download URL over the untracked archive URL, with automatic fallback. The traffic dashboard gained a Total Downloads metric, release downloads chart, and per-release download column.

### GHCR container pull stats

The traffic analytics workflow now also collects Docker pull counts from GHCR (`ghcr.io/trudenboy/sendspin-bt-bridge`), providing visibility into container adoption alongside GitHub traffic data.

## Why this matters

The bug report proxy directly addresses a gap between the project's user base and its issue tracker. Bridge operators are predominantly HA users who manage their setup through a web UI, not through GitHub. Giving them a way to report bugs without creating an account removes friction and should increase the quality of feedback.

The Docker Compose fix was the most-reported installation issue (#114). Ubuntu and Debian are popular Docker hosts, and the AppArmor/seccomp defaults silently prevented Bluetooth initialization with no useful error message. This fix likely unblocks several users who tried and gave up.
