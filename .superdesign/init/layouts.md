# Shared layouts

## Dashboard shell

- Source: `src/sendspin_bridge/web/templates/index.html`
- Description: Single-page dashboard with blue Home Assistant-style header, filter/action toolbar, device grid/list, configuration modal, diagnostics, and mobile bottom navigation.

Relevant shared view selector source:

```html
<div class="view-toggle" style="margin-left:auto">
    <button type="button" class="view-toggle-btn" id="view-grid-btn" data-action="set-view-mode" data-arg="grid" title="Grid view">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M3 3h8v8H3V3zm0 10h8v8H3v-8zm10-10h8v8h-8V3zm0 10h8v8h-8v-8z"/></svg>
    </button>
    <button type="button" class="view-toggle-btn active" id="view-list-btn" data-action="set-view-mode" data-arg="list" title="List view">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z"/></svg>
    </button>
</div>
```

Device configuration layout source:

```js
// Configuration rows place latency controls before Bluetooth actions.
'<div class="bt-row-actions bt-cell">' +
    _renderConfigLatencyControlsHtml(delayVal) +
    bluetoothActions +
'</div>'
```
