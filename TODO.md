# TODO

Roadmap for HA addon standards compliance. Phases are ordered by effort and impact.

## Phase 2: HA Base Images & Build Pipeline

- [ ] **Migrate to HA Debian base images** — switch from `python:3.13-slim` to `ghcr.io/home-assistant/{arch}-base-debian:bookworm`, install Python and all dependencies on top
- [ ] **Adopt `rootfs/` overlay pattern** — move entrypoint scripts into `rootfs/etc/` structure, use single `COPY rootfs /` in Dockerfile *(depends on: base images)*
- [ ] **Merge into single Dockerfile** — eliminate the two-image chain (root Dockerfile → ha-addon/Dockerfile), single Dockerfile in `ha-addon/` with `ARG BUILD_FROM` pattern *(depends on: base images)*
- [ ] **Add Hadolint config** — create `.hadolint.yaml`, add Dockerfile linting to CI

## Phase 3: S6 Overlay & Security

- [ ] **Adopt S6 Overlay** — create `s6-rc.d` service structure for process supervision, set `init: false` in config.yaml *(depends on: base images, rootfs)*
- [ ] **Implement proper signal handling** — S6 SIGTERM handling, clean subprocess shutdown, finish scripts *(depends on: S6 overlay)*
- [ ] **Security hardening** — minimize privileged capabilities, review `SYS_ADMIN` necessity, write proper AppArmor profile *(depends on: S6 overlay)*

## Other

- [ ] **Create `ha-addon/logo.png`** — wide-format logo for HA store listing
- [ ] **Add HA discovery integration** — support HA discovery protocol for auto-configuring Music Assistant connection
