---
title: 2026-04-06 — Idle mode, Docker diet, and unified branding (v2.55.0)
description: v2.55.0 introduced a per-device idle mode system with four strategies (including PA sink suspend and infrasound keepalive), cut Docker image size by 51%, unified all branding to the wave-bridge design, and merged six Dependabot PRs
---

The final day of the week was a feature-driven release: a new idle mode system that replaced two confusing numeric inputs with a single dropdown of four human-readable strategies, a major Docker image diet, and a visual identity unification across all channels. Twelve RC iterations led to v2.55.0 stable.

## What shipped

### Per-device idle mode

The old idle behavior had two independent settings per device: `keepalive_interval` (seconds between silence bursts) and `idle_disconnect_minutes` (timeout before BT disconnect). Users had to understand the interaction between them — setting both was contradictory, setting neither was the implicit "default". The new `idle_mode` enum replaces both with a single dropdown:

| Mode | Behavior | Use case |
|------|----------|----------|
| **default** | No action; speaker's hardware timer decides | Speakers that sleep fine on their own |
| **power_save** | Suspend PA sink after delay → release A2DP → speaker sleeps, BT stays connected | Fast resume without reconnect latency |
| **auto_disconnect** | Full BT disconnect + daemon → null-sink after timeout | Same as old `idle_disconnect_minutes` |
| **keep_alive** | Stream periodic infrasound bursts to keep A2DP active | Speakers that disconnect on digital silence |

**Power save mode** is new. It leverages the SinkMonitor infrastructure from v2.54.0: after the configurable delay (1–60 minutes, default 1 min), the bridge suspends the PulseAudio sink via `asuspend_sink()` (pulsectl API with `pactl suspend-sink` fallback). This releases the A2DP transport so the speaker can enter hardware sleep, but keeps the Bluetooth connection alive. On next play, PA resumes the sink automatically — no reconnect latency.

**Infrasound keepalive** is an improvement to the old keepalive mode. Previously, keepalive streamed pure digital silence — which some speakers ignore (they detect "no meaningful audio" and disconnect anyway). The new implementation generates 2 Hz sine wave bursts at −50 dB: below human hearing threshold but non-zero PCM data that keeps the A2DP transport active.

**Config migration** runs automatically on startup: `keepalive_interval > 0` → `keep_alive`, `idle_disconnect_minutes > 0` → `auto_disconnect`, both zero → `default`. Explicit `idle_mode` values are never overwritten.

The UI dropdown was restyled after rc.1 shipped with an unstyled `<select>` element (rc.2), and the delay unit was changed from seconds to minutes after testing showed that sub-minute granularity wasn't useful (rc.3).

### Docker image −51%

The Docker image shrank from 916 MB to ~450 MB through three rounds of optimization:

**Round 1 (rc.7): Remove system FFmpeg (−37%, 916→580 MB).** Investigation revealed that PyAV wheels bundle their own FFmpeg libraries in `av.libs/`, making the system FFmpeg installation redundant on amd64 and arm64. System FFmpeg is still needed on armv7 (compiled from source since there are no prebuilt wheels).

**Round 2 (rc.9): Deep cleanup (−51%, 916→450 MB).** The `apt-get install pulseaudio-utils` pulls in transitive dependencies for FFmpeg, GStreamer, and various codecs that `pactl` doesn't need at runtime. These are now force-removed after installation. Additionally:
- Debug symbols stripped from all `.so` files (saves ~30 MB)
- Unused Python stdlib modules removed: `ensurepip`, `idlelib`, `lib2to3`, `pydoc_data`, `turtledemo`, `test`
- pip, pygments, numpy test suite, and `__pycache__` directories stripped from the runtime image

**Round 3 (rc.10): Keep libasound2-plugins.** The cleanup in rc.9 accidentally removed `libasound2-plugins`, which provides `libasound_module_pcm_pulse.so` — the ALSA→PulseAudio bridge that sounddevice/PortAudio uses to discover audio sinks. Without it, daemon subprocesses crashed with "No audio output device found". This was the PipeWire-specific failure — on PulseAudio, the direct PA connection works; on PipeWire, PortAudio needs the ALSA→PulseAudio shim.

### Unified branding

All visual assets across the project were replaced with the landing page wave-bridge design (two rounded pillars with three sine-wave curves):

- **HA addon icons** (rc.11): stable=teal-purple gradient, rc=gold, beta=red. Total icon size reduced from 316 KB to 80 KB.
- **All logos and favicons** (rc.12): web UI favicon, docs-site logo, and all remaining assets unified. Total asset size from ~310 KB to ~55 KB.

The channel color differentiation is important for HA users who might have multiple addon channels installed — the icon color immediately identifies which channel a device is running.

### Dependency updates and CI

Six Dependabot PRs merged:
- `dbus-fast` 4.0.0→4.0.4 (D-Bus performance improvements)
- `ruff` 0.11.13→0.15.8 (linter)
- `numpy` pin restored to `<2.0` after rc.5 widened it to `<3.0` and hit the X86_V2 CPU baseline crash on QEMU
- `docker/build-push-action` v6→v7 (Node 24)
- `actions/download-artifact` v4→v8 (hash enforcement)
- `actions/upload-pages-artifact` v3→v4

The NumPy pin deserves a note: numpy 2.x requires the X86_V2 instruction set baseline (POPCNT, SSE4.2) which is unavailable on QEMU's default `qemu64` CPU model and older physical CPUs. This was previously fixed in v2.50.0 but the Dependabot PR widened it back; rc.6 reverted.

### Other fixes

- **Config download 404 in HA addon ingress mode** — hardcoded download path bypassed the ingress `SCRIPT_NAME` prefix; now uses `API_BASE` (rc.4)
- **Auto-expand device detail row** — clicking "Configure" from onboarding or guidance auto-expands the target device row before highlighting it (rc.4)

## Community activity

- **Issue #123**: User @bugensui2022 reported no audio output after installing and uninstalling the bridge on HAOS. Investigation revealed the null-sink fallback (`sendspin_fallback`) was set as the default PA sink and persisted after uninstallation. Resolved with guidance to reset the default sink via `pactl set-default-sink <original_sink>`. This highlighted a gap in the uninstall cleanup — future versions should restore the original default sink on addon removal.
- **Issue #133**: User @mdorchain reported sinks not appearing after host reboot on PipeWire/Ubuntu. The bridge connects to PulseAudio before the BT A2DP sink is created by WirePlumber. Investigation ongoing.

## Release timeline

| Version | Date | Key change |
|---------|------|------------|
| 2.55.0-rc.1 | Apr 6 | idle_mode enum, infrasound keepalive, PA suspend |
| 2.55.0-rc.2 | Apr 6 | Idle mode dropdown styling |
| 2.55.0-rc.3 | Apr 6 | Delay unit: seconds → minutes |
| 2.55.0-rc.4 | Apr 6 | Config download ingress fix, auto-expand CTA |
| 2.55.0-rc.5/6 | Apr 6 | Dependency updates, NumPy <2.0 revert |
| 2.55.0-rc.7 | Apr 6 | Docker −37% (FFmpeg removal) |
| 2.55.0-rc.9 | Apr 6 | Docker −51% (deep cleanup) |
| 2.55.0-rc.10 | Apr 6 | Keep libasound2-plugins |
| 2.55.0-rc.11/12 | Apr 6 | Unified branding |
| **2.55.0** | **Apr 6** | **Stable release** |

## Why this matters

The idle mode system is the culmination of the SinkMonitor work from v2.53–2.54. Without PA sink state awareness, power save mode (suspend/resume) would be impossible. The four-mode enum gives users a clear mental model instead of two interacting numeric parameters.

The Docker diet matters for Raspberry Pi users where SD card space and download time are real constraints. Going from 916 MB to 450 MB means faster addon updates and less wear on flash storage.

The unified branding closes a visual inconsistency: the landing page had the wave-bridge design, but the HA addon and web UI still used the old bridge+equalizer icon. Now everything is visually coherent.

The twelve-RC day is a record for the project — but each RC addressed a real issue discovered in testing. The rapid iteration cycle (code → RC → test → fix → next RC) keeps quality high without multi-day stabilization periods.

## Follow-up

Issue #133 (sink not available after reboot on PipeWire) needs investigation — likely requires WirePlumber readiness detection or a delayed sink scan at startup. The null-sink cleanup gap from #123 should be addressed in a future release with proper uninstall hooks.

Test suite: 1088 Python tests. Total releases since v2.52.0: 33 (8 stable + 25 RC).
