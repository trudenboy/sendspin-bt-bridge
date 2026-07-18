# Page dependency trees

## `/` Dashboard

Entry: `src/sendspin_bridge/web/templates/index.html`

Dependencies:

- `src/sendspin_bridge/web/templates/index.html`
  - `src/sendspin_bridge/web/static/style.css`
  - `src/sendspin_bridge/web/static/app.js`
  - `src/sendspin_bridge/web/static/bridge-logo-header.png`
  - `src/sendspin_bridge/web/static/favicon.svg`

Target render branches:

- Grid device card: `app.js` `buildDeviceCard()` and `populateDeviceCard()`.
- List device row: `app.js` `buildListView()`.
- Configuration delay UI: `app.js` `_renderConfigLatencyControlsHtml()`, `_populateConfigLatencyControls()`, and live action handlers.
- Delay styles: final `.bt-latency-controls` block in `style.css`.

## `/login`

Entry: `src/sendspin_bridge/web/templates/login.html`

Dependencies:

- `src/sendspin_bridge/web/templates/login.html`
  - inline styles and scripts
