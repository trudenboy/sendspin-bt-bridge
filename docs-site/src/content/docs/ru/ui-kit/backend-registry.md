---
title: Паттерн Backend Descriptor
description: Как добавить новый тип аудиобэкенда в UI
---

## Обзор

UI использует паттерн **реестра дескрипторов** для отрисовки конфигурации, статуса и визуализации сигнального пути без жёсткого кодирования типов бэкенда в компонентах.

📄 **Файл:** `ui/src/types/backend-registry.ts`

Центральный `BACKEND_REGISTRY` сопоставляет каждую строку `BackendType` с **объектом-дескриптором**, который сообщает UI всю необходимую информацию для отрисовки этого бэкенда.

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

## Структура дескриптора

Каждая запись в `BACKEND_REGISTRY` реализует `BackendDescriptor`:

```typescript
interface BackendDescriptor {
  type: BackendType
  labelKey: string                 // ключ перевода i18n
  icon: Component                  // иконка Lucide Vue
  color: string                    // токен: 'primary', 'success' и т.д.
  configFields: BackendConfigField[]
  statusFields: BackendStatusField[]
  signalPath: SignalPathSegment[]
}
```

### Поля конфигурации

Определяют, что пользователь настраивает для данного бэкенда:

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

### Поля статуса

Определяют, какие поля runtime-статуса отображать:

```typescript
interface BackendStatusField {
  key: string
  labelKey: string
  format?: 'text' | 'badge' | 'code'
}
```

### Сегменты сигнального пути

Определяют участки аудиопотока, отрисовываемые компонентом `SbSignalPath`:

```typescript
interface SignalPathSegment {
  id: string
  labelKey: string
}
```

## Зарегистрированные бэкенды

| Тип | Иконка | Цвет | Сигнальный путь |
|-----|--------|------|-----------------|
| `bluetooth_a2dp` | Bluetooth | `primary` | MA → SendSpin → Подпроцесс → Pulse Sink → Колонка |
| `local_sink` | Speaker | `success` | MA → SendSpin → Подпроцесс → Локальный Sink |
| `usb_audio` | Usb | `accent` | MA → SendSpin → Подпроцесс → USB-устройство |
| `virtual_sink` | AudioLines | `info` | MA → SendSpin → Подпроцесс → Виртуальный Sink |
| `snapcast_client` | Radio | `warning` | MA → SendSpin → Snapserver → Клиент |
| `vban` | Podcast | `error` | MA → SendSpin → VBAN-поток → Приёмник |
| `le_audio` | Headphones | `primary` | MA → SendSpin → Подпроцесс → LE Sink |

## Добавление нового типа бэкенда

Добавление нового аудиобэкенда в UI выполняется за четыре шага:

### 1. Добавьте тип

Расширьте объединение `BackendType` в `ui/src/types/backend-registry.ts`:

```typescript
type BackendType =
  | 'bluetooth_a2dp'
  | 'local_sink'
  // ... существующие типы ...
  | 'my_new_backend'   // ← добавьте сюда
```

### 2. Добавьте дескриптор

Добавьте запись в `BACKEND_REGISTRY`:

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

### 3. Добавьте ключи i18n

Добавьте ключи перевода в файлы локализации (например, `ui/src/i18n/en.ts` и `ui/src/i18n/ru.ts`).

### 4. Готово

Все UI-компоненты, использующие реестр — карточки устройств, формы конфигурации, панели статуса, диаграммы сигнального пути — автоматически отрисуют новый бэкенд. Изменения компонентов не требуются.

## Вспомогательные функции

Реестр экспортирует три функции-помощника:

```typescript
// Возвращает дескриптор, с фолбэком на bluetooth_a2dp
getBackendDescriptor(type: string): BackendDescriptor

// Возвращает компонент иконки Lucide
getBackendIcon(type: string): Component

// Возвращает строку цветового токена
getBackendColor(type: string): string
```
