# Sendspin BT Bridge design system

## Product context

Operator dashboard for routing synchronized Music Assistant audio to Bluetooth speakers. Device cards must remain compact, legible at a glance, and safe for live adjustments. Grid, desktop list and portrait mobile are first-class layouts.

## Visual language

- Preserve the existing Home Assistant-inspired visual identity.
- System UI font stack; Roboto-compatible appearance.
- Primary blue `#03a9f4`, darker blue `#0288d1`, apply accent orange `#ff9800`.
- Light cards `#ffffff` on `#fafafa`; dark cards `#1c1c1c` on `#111111`.
- Text `#212121`/`#727272` in light and `#e1e1e1`/`#9b9b9b` in dark.
- Cards use 12px radius, subtle HA shadow, and divider borders.
- Buttons are compact outlined controls with 30–40px minimum hit height. Primary blue identifies the live delay stepper and active step size.
- Latency values always use tabular numerals and an explicit `ms` suffix.

## Delay interaction requirements

- Controls live in Configuration → Devices, inside each row's Actions cell immediately before Bluetooth actions.
- Grid and list playback views do not duplicate delay controls.
- Desktop controls fit in one horizontal row without table overflow.
- Current delay is the visual center of the tuning row; recommendations are not displayed or applied from this panel.
- Preserve direct live tuning, the metronome action, and microphone comparison. Metronome and microphone actions use the project's real compact SVG symbols rather than text labels.
- Current implementation uses one segmented `− | value | +` stepper and a shared explicit `±10`/`±50` selector. Shift temporarily selects 50 ms and press-and-hold repeats.
- Mobile keeps the delay controls on one line and may move Bluetooth actions below them.
