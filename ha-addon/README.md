# Sendspin Bluetooth Bridge

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]
![Supports armv7 Architecture][armv7-shield]

Bridge Music Assistant's Sendspin protocol to Bluetooth speakers.
Stream audio from Music Assistant to any Bluetooth A2DP speaker
connected to your Home Assistant host.

## About

This add-on allows you to use Bluetooth speakers as audio output
players in Music Assistant. It connects to Music Assistant via the
Sendspin protocol and routes audio streams to paired Bluetooth
devices via PulseAudio/PipeWire.

Key features:
- Multi-speaker support — each speaker appears as a separate player in Music Assistant
- Automatic Bluetooth reconnection
- Web UI for status monitoring and configuration (via HA Ingress)
- mDNS auto-discovery of Music Assistant server
- Volume control through Music Assistant or direct PulseAudio
- Music Assistant reconfigure flow in **Configuration → Music Assistant**
- Guided onboarding, recovery actions, release/reclaim controls, and diagnostics-backed bug report prefills in the web UI

## Documentation

For full documentation, see [DOCS.md](DOCS.md) or visit the
[documentation site](https://trudenboy.github.io/sendspin-bt-bridge).

## Update channels

- The checked-in addon manifest in this repository is the **stable** Home Assistant addon variant.
- The installed addon track is determined by the addon variant you install from the Home Assistant store.
- The bridge UI only indicates the current track and update guidance; it does not switch the installed addon track.
- When RC or Beta addon variants are published, switching tracks means installing the matching addon variant from the Home Assistant store.
- Stable / RC / Beta addon variants use different default HA ingress ports and different default player listen-port ranges, so they can run side by side on one HAOS host.
- Stable starts automatically after host boot; RC and Beta default to manual start so prerelease tracks stay opt-in.
- HA Ingress always keeps using the fixed track-specific port (`8080` stable, `8081` RC, `8082` beta). A custom `WEB_PORT` only adds an extra direct listener; it does not replace Ingress.
- In addon mode, auth is always enforced by Home Assistant / Ingress; the standalone password toggle does not apply here.
- Silent Home Assistant token bootstrap for Music Assistant only works through the addon/Ingress flow, so related UI helpers are intentionally addon-scoped.
- Do **not** configure the same Bluetooth speaker in more than one addon variant at the same time.

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[armv7-shield]: https://img.shields.io/badge/armv7-yes-green.svg
