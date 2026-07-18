# Shared UI components

The dashboard uses vanilla JavaScript string renderers rather than a component library. The delay control is the reusable UI primitive relevant to this design task.

## ConfigLatencyControls

- Source: `src/sendspin_bridge/web/static/app.js` `_renderConfigLatencyControlsHtml()`
- Description: Compact per-device live delay controls rendered in Configuration → Devices actions.
- Props: current delay.

```js
function _renderConfigLatencyControlsHtml(delayMs) {
    return '<div class="bt-latency-controls">' +
      '<div class="bt-latency-stepper">' +
        '<button data-action="config-latency-nudge" data-arg="-1">−</button>' +
        '<span class="bt-latency-value">210 ms</span>' +
        '<button data-action="config-latency-nudge" data-arg="1">+</button>' +
      '</div>' +
        '<button data-action="toggle-config-latency-step">±10</button>' +
        '<button type="button" class="action-btn latency-test-clicks" aria-label="Start metronome">[metronome SVG]</button>' +
        '<button type="button" class="action-btn latency-mic-compare" aria-label="Compare speakers using microphone">[microphone SVG]</button>' +
      '</div>';
}
```
