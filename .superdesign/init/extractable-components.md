# Extractable components

## DashboardHeader

- Source: `src/sendspin_bridge/web/templates/index.html`
- Category: layout
- Description: Blue application header with bridge identity, runtime badges, navigation and theme controls.
- Extractable props: activeSection, showUpdateBadge.
- Hardcoded: logo, navigation labels, colors, icons.

## DeviceCard

- Source: `src/sendspin_bridge/web/static/app.js`
- Category: basic
- Description: Speaker card with identity, status, transport, volume and device actions.
- Extractable props: isExpanded, isPlaying.
- Hardcoded: transport/action icon shapes and button labels.

## ConfigLatencyControls

- Source: `src/sendspin_bridge/web/static/app.js`
- Category: basic
- Description: Per-speaker live delay tuning controls embedded in Configuration → Devices actions.
- Extractable props: currentDelay, activeStep, metronomeActive, runtimeAvailable.
- Hardcoded: 10/50 ms increments and the metronome/microphone SVG actions.
