---
title: Backend Descriptor Pattern
description: How to add new audio backend types to the UI
---

## Overview

The UI uses a **descriptor registry** pattern to render backend-specific configuration, status, and signal-path visualizations without hardcoding any backend type into components.

📄 **Reference:** `ui/src/types/backend-registry.ts`

The central `BACKEND_REGISTRY` maps each `BackendType` string to a **descriptor object** that tells the UI everything it needs to render that backend.

## BackendType

```typescript
type BackendType =
  | 'bluetooth_a2dp'
  | 'local_sink'
  | 'usb_audio'
  | 'virtual_sink'
  | 'snapcast_client'
  | 'vban'
  | 'le_audio'
```

## Descriptor Structure

Each entry in `BACKEND_REGISTRY` implements `BackendDescriptor`:

```typescript
interface BackendDescriptor {
  type: BackendType
  labelKey: string                 // i18n translation key
  icon: Component                  // Lucide Vue icon component
  color: string                    // Token: 'primary', 'success', etc.
  configFields: BackendConfigField[]
  statusFields: BackendStatusField[]
  signalPath: SignalPathSegment[]
}
```

### Config Fields

Define what the user configures for this backend:

```typescript
interface BackendConfigField {
  key: string
  labelKey: string
  type: 'text' | 'number' | 'toggle' | 'select' | 'slider'
  options?: { value: string; labelKey: string }[]
  min?: number
  max?: number
  step?: number
  required?: boolean
}
```

### Status Fields

Define what runtime status fields to display:

```typescript
interface BackendStatusField {
  key: string
  labelKey: string
  format?: 'text' | 'badge' | 'code'
}
```

### Signal Path

Define the audio flow segments rendered by `SbSignalPath`:

```typescript
interface SignalPathSegment {
  id: string
  labelKey: string
}
```

## Registered Backends

| Type | Icon | Color | Signal Path |
|------|------|-------|-------------|
| `bluetooth_a2dp` | Bluetooth | `primary` | MA → SendSpin → Subprocess → Pulse Sink → Speaker |
| `local_sink` | Speaker | `success` | MA → SendSpin → Subprocess → Local Sink |
| `usb_audio` | Usb | `accent` | MA → SendSpin → Subprocess → USB Device |
| `virtual_sink` | AudioLines | `info` | MA → SendSpin → Subprocess → Virtual Sink |
| `snapcast_client` | Radio | `warning` | MA → SendSpin → Snapserver → Client |
| `vban` | Podcast | `error` | MA → SendSpin → VBAN Stream → Receiver |
| `le_audio` | Headphones | `primary` | MA → SendSpin → Subprocess → LE Sink |

## Adding a New Backend Type

Adding a new audio backend to the UI takes four steps:

### 1. Add the type

Extend the `BackendType` union in `ui/src/types/backend-registry.ts`:

```typescript
type BackendType =
  | 'bluetooth_a2dp'
  | 'local_sink'
  // ... existing types ...
  | 'my_new_backend'   // ← add here
```

### 2. Add the descriptor

Add an entry to `BACKEND_REGISTRY`:

```typescript
my_new_backend: {
  type: 'my_new_backend',
  labelKey: 'backend.my_new_backend',
  icon: markRaw(MyIcon),
  color: 'info',
  configFields: [
    {
      key: 'host',
      labelKey: 'backend.my_new_backend.host',
      type: 'text',
      required: true,
    },
  ],
  statusFields: [
    {
      key: 'player_state',
      labelKey: 'backend.status.player_state',
      format: 'badge',
    },
  ],
  signalPath: [
    { id: 'ma',     labelKey: 'signal.ma' },
    { id: 'daemon', labelKey: 'signal.sendspin' },
    { id: 'output', labelKey: 'backend.my_new_backend.output' },
  ],
},
```

### 3. Add i18n keys

Add the translation keys used above to the locale files (e.g., `ui/src/i18n/en.ts` and `ui/src/i18n/ru.ts`).

### 4. Done

All UI components that consume the registry — device cards, config forms, status panels, signal path diagrams — will automatically render the new backend. No component changes required.

## Helper Functions

The registry exports three convenience functions:

```typescript
// Returns the descriptor, falling back to bluetooth_a2dp
getBackendDescriptor(type: string): BackendDescriptor

// Returns the Lucide icon component
getBackendIcon(type: string): Component

// Returns the color token string
getBackendColor(type: string): string
```
