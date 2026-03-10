# TODO

Roadmap for HA addon standards compliance and improvements.

## Done (v2.15.0–v2.18.3)

- [x] **35 unit tests, diagnostics enrichment, TOCTOU fix, MA WS response matching** (v2.15.0)
- [x] **Multi-arch Docker builds** — amd64/arm64/armv7 (v2.15.2)
- [x] **Fix re-anchor loop on stream start** — sendspin-cli 5.1.4 preserves cooldown timer across `clear()` calls (v2.15.3)
- [x] **Split armv7 CI into separate workflow** — amd64/arm64 publish immediately; armv7 builds independently via QEMU (v2.15.4)
- [x] **Fix AppArmor profile** — temporarily disabled (`apparmor: false`), was blocking Python imports on HAOS (v2.15.5)
- [x] **Auto-unmute BT sink, switched to PyPI sendspin** (v2.15.6)
- [x] **Security audit** — 42 issues fixed, 65 new tests (107 total), `SYS_ADMIN` capability removed (v2.16.0)
- [x] **PyAV armv7l compatibility fix** (v2.16.1)
- [x] **RPi preflight script, `/api/preflight`, startup diagnostics, RPi & Docker docs** (v2.16.2)
- [x] **Add Hadolint config** — `.hadolint.yaml` + Dockerfile linting in CI (v2.16.3)
- [x] **Create `ha-addon/logo.png`** — wide-format logo for HA store listing (v2.16.3)
- [x] **One-liner RPi installer** — `scripts/rpi-install.sh`: install Docker, generate compose, pair BT, start (v2.16.3)
- [x] **MA auto-discovery & auto-login** — mDNS discovery of MA servers + passwordless auth via Ingress JSONRPC in addon mode (v2.17.0–v2.18.3)

## Next

- [ ] **Add HA discovery integration** — support HA discovery protocol for auto-configuring MA connection

## Future

- [ ] **Migrate to HA Debian base images** — switch from `python:3.12-slim` to `ghcr.io/home-assistant/{arch}-base-debian:bookworm`
- [ ] **Adopt `rootfs/` overlay pattern** — move entrypoint scripts into `rootfs/etc/` structure *(depends on: base images)*
- [ ] **Merge into single Dockerfile** — eliminate two-image chain, single `ha-addon/Dockerfile` with `ARG BUILD_FROM` *(depends on: base images)*
- [ ] **Adopt S6 Overlay** — `s6-rc.d` service structure for process supervision *(depends on: base images, rootfs)*
- [ ] **Implement proper signal handling** — S6 SIGTERM handling, clean subprocess shutdown *(depends on: S6 overlay)*
- [ ] **Write proper AppArmor profile** — complain mode → audit log → tested whitelist *(depends on: S6 overlay)*
- [ ] **Web UI setup wizard** — first-run wizard: detect speakers, pair, configure MA — all from the browser
