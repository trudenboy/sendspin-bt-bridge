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

## Documentation

For full documentation, see [DOCS.md](DOCS.md) or visit the
[documentation site](https://trudenboy.github.io/sendspin-bt-bridge).

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[armv7-shield]: https://img.shields.io/badge/armv7-yes-green.svg
