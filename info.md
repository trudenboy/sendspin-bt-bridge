# Sendspin BT Bridge — Home Assistant integration

Native Home Assistant integration for the
[Sendspin BT Bridge](https://github.com/trudenboy/sendspin-bt-bridge),
a Dockerised service that turns any Bluetooth speaker into a
[Music Assistant](https://music-assistant.io/) player and keeps the
audio playing alongside MA's other player types (Sonos, Chromecast,
Squeezelite, …).

This integration adds the bridge as a first-class device in Home
Assistant, exposing every per-speaker control as a real entity you
can put on a dashboard or use in an automation:

- **Switches.** Enable / standby / power save / Bluetooth management,
  all idempotent — re-asserting the target state is a no-op rather
  than a 409.
- **Buttons.** Reconnect, disconnect, claim audio (steal a
  multipoint speaker back from a phone).
- **Numbers / selects.** Idle mode, keep-alive method, static
  delay (ms), power-save delay (min) — writable from HA.
- **Sensors / binary sensors.** Signal strength (dBm and badge),
  battery level, audio codec, link state, sync health, last error.

Disabled or sleeping speakers stay reachable from the dashboard so
the operator can wake or re-enable them with one tap.

## Prerequisites

You need the **Sendspin BT Bridge** itself running somewhere reachable
from Home Assistant. Pick one:

- **Home Assistant Add-on** (HAOS / Supervised) — install from the
  [add-on repository](https://github.com/trudenboy/sendspin-bt-bridge).
  HACS auto-discovers and pairs the bridge with no manual token.
- **Docker / Proxmox LXC** standalone — see the
  [README](https://github.com/trudenboy/sendspin-bt-bridge#deployment)
  for the supported deployment topologies.

## Setup

1. Install this integration via HACS (you're reading the right page).
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → "Sendspin BT
   Bridge"**.
4. On HAOS the bridge is auto-discovered. Otherwise paste the bridge
   URL and a bearer token from *Settings → Home Assistant → Generate
   token* in the bridge web UI.

The bridge ships an MQTT path too — that's installed independently
through the bridge's *Settings → Home Assistant* tab and does not
require this custom_component.

## Documentation

Full project documentation, troubleshooting, and the list of every
configuration option lives at
[https://trudenboy.github.io/sendspin-bt-bridge](https://trudenboy.github.io/sendspin-bt-bridge).
