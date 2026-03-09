# TODO

Roadmap for HA addon standards compliance and improvements.

## Done (v2.15.3–v2.15.5)

- [x] **Fix re-anchor loop on stream start** — sendspin-cli 5.1.4 preserves cooldown timer across `clear()` calls
- [x] **Split armv7 CI into separate workflow** — amd64/arm64 publish immediately; armv7 builds independently via QEMU
- [x] **Fix AppArmor profile** — custom profile was too restrictive, blocked Python shared libs and module imports on HAOS; temporarily disabled (`apparmor: false`)

## Phase 2: HA Base Images & Build Pipeline

- [ ] **Migrate to HA Debian base images** — switch from `python:3.12-slim` to `ghcr.io/home-assistant/{arch}-base-debian:bookworm`, install Python and all dependencies on top
- [ ] **Adopt `rootfs/` overlay pattern** — move entrypoint scripts into `rootfs/etc/` structure, use single `COPY rootfs /` in Dockerfile *(depends on: base images)*
- [ ] **Merge into single Dockerfile** — eliminate the two-image chain (root Dockerfile → ha-addon/Dockerfile), single Dockerfile in `ha-addon/` with `ARG BUILD_FROM` pattern *(depends on: base images)*
- [ ] **Add Hadolint config** — create `.hadolint.yaml`, add Dockerfile linting to CI

## Phase 3: S6 Overlay & Security

- [ ] **Adopt S6 Overlay** — create `s6-rc.d` service structure for process supervision, set `init: false` in config.yaml *(depends on: base images, rootfs)*
- [ ] **Implement proper signal handling** — S6 SIGTERM handling, clean subprocess shutdown, finish scripts *(depends on: S6 overlay)*
- [ ] **Write proper AppArmor profile** — run in complain mode on HAOS, collect denied ops from audit log, build tested whitelist. Currently disabled since v2.15.5 *(depends on: S6 overlay)*
- [ ] **Security hardening** — minimize privileged capabilities, review `SYS_ADMIN` necessity *(depends on: AppArmor profile)*

## Other

- [ ] **Create `ha-addon/logo.png`** — wide-format logo for HA store listing
- [ ] **Add HA discovery integration** — support HA discovery protocol for auto-configuring Music Assistant connection
- [ ] **Revert to PyPI sendspin** — when upstream publishes 5.1.4, revert `requirements.txt` back to `sendspin>=5.1.4,<6` and remove `git` from Dockerfile builder stage
