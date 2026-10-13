---
title: Audio Outputs
description: Playing to a local audio output (USB DAC, sound card, HDMI) as a Music Assistant player
---

Besides Bluetooth speakers, the bridge can play to an **audio output of the machine it runs on** — a USB DAC, a sound card or an HDMI output. Each output becomes its own player in Music Assistant, with its own volume, delay and room.

## Adding an output

1. Open **Speakers → Add speaker** and switch to the **Audio output** tab.
2. Pick an output from the list and press **Add**. Outputs already used by another player are marked.
3. Choose a name (and optionally a room) and press **Add to bridge**.

Bluetooth outputs and the bridge's own internal sinks are never listed.

## What to expect

- **Home Assistant add-on.** Only the outputs that Home Assistant's audio system exposes to the add-on appear in the list. If your DAC is not there, check **Settings → System → Hardware → Audio** in Home Assistant first.
- **Not bit-perfect.** Audio goes through PulseAudio or PipeWire, which may resample it and mix it with other applications. The bridge does not take exclusive control of the device.
- **Volume.** By default the bridge handles volume itself and does not touch the output's system volume, because the output may be shared with other applications. Choose the system volume in the player's settings only if you want the player's volume slider to move the output's volume.
- **Unplugging.** When the output disappears, the card shows **Output missing** and playback waits. The player stays in its Music Assistant groups, silent, until the output is back. Replugging the device resumes the player automatically; if the system gave the output a new name, the bridge finds it again by its hardware identity and remembers the new name.
- **Never the default output.** A player never falls back to the default output: without its own output it does not start.
- **Warnings.** Diagnostics warn when an output is muted, at 0 % volume or reports no connected port. With the `PULSE_SINK_RESET_ON_START` setting on, the bridge suspends and resumes each output once before its player starts, which wakes outputs that got stuck (off by default).

## For developers

Setting the `SENDSPIN_MOCK_SINKS` environment variable to a file in `pactl list sinks` format replaces the real list of outputs. It is meant for development and tests only.
