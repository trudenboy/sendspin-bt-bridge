# Theme

Source: `src/sendspin_bridge/web/static/style.css`. The stylesheet is monolithic; these are the complete theme tokens used by the dashboard and delay controls.

```css
:root {
    --primary-color: #03a9f4;
    --dark-primary-color: #0288d1;
    --accent-color: #ff9800;
    --primary-text-color: #212121;
    --secondary-text-color: #727272;
    --disabled-text-color: #bdbdbd;
    --primary-background-color: #fafafa;
    --secondary-background-color: #e5e5e5;
    --card-background-color: #ffffff;
    --ha-card-border-radius: 12px;
    --divider-color: rgba(0, 0, 0, .12);
    --error-color: #db4437;
    --success-color: #43a047;
    --warning-color: #ffa600;
    --info-color: #039be5;
    --ha-card-box-shadow: 0 2px 2px 0 rgba(0,0,0,.14), 0 1px 5px 0 rgba(0,0,0,.12), 0 3px 1px -2px rgba(0,0,0,.2);
    --app-header-background-color: var(--primary-color);
    --app-header-text-color: #fff;
    --badge-font-size: 11px;
    --badge-font-weight: 600;
    --badge-min-height: 24px;
    --badge-padding-y: 4px;
    --badge-padding-x: 9px;
    --badge-radius: 999px;
}
html.theme-dark {
    color-scheme: dark;
    --primary-background-color: #111111;
    --secondary-background-color: #202020;
    --card-background-color: #1c1c1c;
    --primary-text-color: #e1e1e1;
    --secondary-text-color: #9b9b9b;
    --disabled-text-color: rgba(225,225,225,.5);
    --divider-color: rgba(225,225,225,.12);
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
}
```

Component conventions: 12px card radius, 32–40px controls, neutral outlined action buttons, orange accent for apply, red/warning only for destructive or active stop states, tabular numerals for latency.
