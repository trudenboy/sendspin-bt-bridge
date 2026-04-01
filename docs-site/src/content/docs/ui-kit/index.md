---
title: UI Kit
description: Reusable Vue 3 component library for the Sendspin operator console
---

## Overview

The Sendspin UI Kit is a shared component library located in `ui/src/kit/`. Every component uses the `Sb` prefix to distinguish kit primitives from application-level views.

**Tech stack:** Vue 3 Composition API, TypeScript, Tailwind CSS 4.

The library contains **21 components** organized into four categories:

| Category | Components |
|----------|------------|
| **Display** | SbBadge, SbSpinner, SbStatusDot, SbTooltip, SbTimeline, SbSignalPath |
| **Container** | SbCard, SbTabs, SbTable, SbEmptyState, SbFilterBar |
| **Overlay** | SbDialog, SbDrawer, SbToast, SbToastContainer |
| **Form** | SbButton, SbInput, SbToggle, SbSlider, SbDropdown, SbDropdownItem |

## Quick Reference

### Display

| Component | Description | Key Props |
|-----------|-------------|-----------|
| `SbBadge` | Status badge with tone variants | `tone`, `size`, `dot`, `removable` |
| `SbSpinner` | Animated loading indicator | `size`, `label` |
| `SbStatusDot` | Colored dot for device state | `status`, `pulse`, `size` |
| `SbTooltip` | Hover/focus tooltip | `content`, `position`, `delay` |
| `SbTimeline` | Vertical event timeline | `events`, `maxItems` |
| `SbSignalPath` | Audio signal flow diagram | `segments`, `direction` |

### Container

| Component | Description | Key Props |
|-----------|-------------|-----------|
| `SbCard` | Grouped content with optional header/footer | `collapsible`, `collapsed`, `loading`, `padding` |
| `SbTabs` | Tabbed interface with keyboard navigation | `tabs` (v-model: active tab ID) |
| `SbTable` | Data table with sorting and selection | `columns`, `rows`, `sortBy`, `sortDir`, `selectable`, `rowKey` |
| `SbEmptyState` | Placeholder for empty lists | `title`, `description`, `icon` |
| `SbFilterBar` | Search and filter with chips | `filters`, `placeholder` (v-model: search) |

### Overlay

| Component | Description | Key Props |
|-----------|-------------|-----------|
| `SbDialog` | Centered modal with focus trap | `modelValue`, `title`, `size`, `closable`, `persistent` |
| `SbDrawer` | Side panel with slide animation | `modelValue`, `title`, `side`, `width`, `closable` |
| `SbToast` | Single notification message | `id`, `message`, `type`, `duration`, `closable` |
| `SbToastContainer` | Fixed container for all toasts | — (uses notification store) |

### Form

| Component | Description | Key Props |
|-----------|-------------|-----------|
| `SbButton` | Clickable button with variants | `variant`, `size`, `loading`, `disabled`, `icon` |
| `SbInput` | Text input with label and validation | `label`, `type`, `error`, `hint`, `disabled`, `required` |
| `SbToggle` | Switch/toggle component | `label`, `disabled`, `size` (v-model: boolean) |
| `SbSlider` | Range slider with formatted value | `min`, `max`, `step`, `label`, `showValue`, `formatValue` |
| `SbDropdown` | Dropdown menu trigger | `align`, `width` |
| `SbDropdownItem` | Item inside a dropdown menu | `disabled`, `destructive` |

## Usage

Import components from the `@/kit` barrel export:

```vue
<script setup lang="ts">
import { SbButton, SbBadge, SbCard } from '@/kit'
</script>

<template>
  <SbCard>
    <SbBadge tone="success" dot>Connected</SbBadge>
    <SbButton variant="primary" @click="handleClick">
      Start Stream
    </SbButton>
  </SbCard>
</template>
```

All components support dark mode automatically via the design token system — see [Design Tokens](./design-tokens/) for details.
