---
title: Design Tokens
description: Color palette, typography, spacing, and dark mode tokens
---

## Overview

The UI Kit uses **Tailwind CSS v4** tokens defined in a CSS `@theme {}` block rather than a JavaScript config file. All tokens live in a single file:

📄 **Reference:** `ui/src/app.css`

## Color Palette

Colors are defined as CSS custom properties inside `@theme {}`:

```css
@theme {
  --color-primary: #03a9f4;
  --color-primary-dark: #0288d1;
  --color-accent: #ff9800;

  --color-success: #43a047;
  --color-warning: #ffa600;
  --color-error: #db4437;
  --color-info: #039be5;
}
```

| Token | Color | Usage |
|-------|-------|-------|
| `primary` | Blue (`#03a9f4`) | Buttons, links, active states |
| `accent` | Orange (`#ff9800`) | Highlights, secondary actions |
| `success` | Green (`#43a047`) | Connected states, confirmations |
| `warning` | Orange (`#ffa600`) | Degraded states, caution badges |
| `error` | Red (`#db4437`) | Errors, destructive actions |
| `info` | Blue (`#039be5`) | Informational badges, tooltips |

### Surface & Text Colors

```css
@theme {
  /* Surfaces — light */
  --color-surface: #fafafa;
  --color-surface-secondary: #e5e5e5;
  --color-surface-card: #ffffff;

  /* Text — light */
  --color-text-primary: #212121;
  --color-text-secondary: #727272;
  --color-text-disabled: #bdbdbd;

  /* Code blocks */
  --color-code-bg: #1e293b;
  --color-code-text: #e2e8f0;
}
```

## Dark Mode

Dark mode uses Tailwind v4's `@custom-variant` directive:

```css
@custom-variant dark (&:where(.dark, .dark *));
```

This activates when the `.dark` class is present on an ancestor element. All `dark:` utilities in Tailwind apply automatically — no extra JavaScript is needed.

## Tone Utilities

The `tone-*` utilities are used by `SbBadge` and other status-aware components to apply a consistent semantic background + text color:

| Utility | Background | Text Color |
|---------|-----------|------------|
| `tone-success` | Green at 12% opacity | `--color-success` |
| `tone-warning` | Orange at 12% opacity | `--color-warning` |
| `tone-error` | Red at 12% opacity | `--color-error` |
| `tone-info` | Blue at 12% opacity | `--color-info` |
| `tone-neutral` | Gray at 10% opacity | `--color-text-secondary` |

Usage example:

```vue
<SbBadge tone="success" dot>Streaming</SbBadge>
<SbBadge tone="error">Offline</SbBadge>
```

## Typography

The font stack uses system fonts for optimal rendering:

```css
@theme {
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI',
               Helvetica, Arial, sans-serif;
  --font-mono: 'SF Mono', SFMono-Regular, ui-monospace,
               Menlo, Consolas, monospace;
}
```

## Border Radius

Radius tokens provide consistent rounding across the UI:

```css
@theme {
  --radius-card: 12px;
  --radius-badge: 999px;
  --radius-button: 8px;
  --radius-input: 8px;
}
```

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-card` | `12px` | Cards, dialogs, drawers |
| `--radius-badge` | `999px` | Badges (pill shape) |
| `--radius-button` | `8px` | Buttons |
| `--radius-input` | `8px` | Inputs, selects, toggles |
