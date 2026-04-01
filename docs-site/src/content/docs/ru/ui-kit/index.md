---
title: UI Kit
description: Библиотека переиспользуемых Vue 3 компонентов для операторской консоли Sendspin
---

## Обзор

UI Kit Sendspin — это библиотека общих компонентов, расположенная в `ui/src/kit/`. Все компоненты используют префикс `Sb` для отличия примитивов кита от прикладных представлений.

**Стек технологий:** Vue 3 Composition API, TypeScript, Tailwind CSS 4.

Библиотека содержит **21 компонент** в четырёх категориях:

| Категория | Компоненты |
|-----------|------------|
| **Отображение** | SbBadge, SbSpinner, SbStatusDot, SbTooltip, SbTimeline, SbSignalPath |
| **Контейнеры** | SbCard, SbTabs, SbTable, SbEmptyState, SbFilterBar |
| **Оверлеи** | SbDialog, SbDrawer, SbToast, SbToastContainer |
| **Формы** | SbButton, SbInput, SbToggle, SbSlider, SbDropdown, SbDropdownItem |

## Справочник компонентов

### Отображение

| Компонент | Описание | Основные пропсы |
|-----------|----------|-----------------|
| `SbBadge` | Статусный бейдж с тоновыми вариантами | `tone`, `size`, `dot`, `removable` |
| `SbSpinner` | Анимированный индикатор загрузки | `size`, `label` |
| `SbStatusDot` | Цветная точка состояния устройства | `status`, `pulse`, `size` |
| `SbTooltip` | Всплывающая подсказка при наведении/фокусе | `content`, `position`, `delay` |
| `SbTimeline` | Вертикальная лента событий | `events`, `maxItems` |
| `SbSignalPath` | Диаграмма пути аудиосигнала | `segments`, `direction` |

### Контейнеры

| Компонент | Описание | Основные пропсы |
|-----------|----------|-----------------|
| `SbCard` | Группировка контента с опциональным заголовком/подвалом | `collapsible`, `collapsed`, `loading`, `padding` |
| `SbTabs` | Вкладки с клавиатурной навигацией | `tabs` (v-model: ID активной вкладки) |
| `SbTable` | Таблица данных с сортировкой и выделением | `columns`, `rows`, `sortBy`, `sortDir`, `selectable`, `rowKey` |
| `SbEmptyState` | Заглушка для пустых списков | `title`, `description`, `icon` |
| `SbFilterBar` | Поиск и фильтрация с чипсами | `filters`, `placeholder` (v-model: поиск) |

### Оверлеи

| Компонент | Описание | Основные пропсы |
|-----------|----------|-----------------|
| `SbDialog` | Центрированный модал с ловушкой фокуса | `modelValue`, `title`, `size`, `closable`, `persistent` |
| `SbDrawer` | Боковая панель с анимацией скольжения | `modelValue`, `title`, `side`, `width`, `closable` |
| `SbToast` | Одиночное уведомление | `id`, `message`, `type`, `duration`, `closable` |
| `SbToastContainer` | Фиксированный контейнер для всех тостов | — (используется хранилище уведомлений) |

### Формы

| Компонент | Описание | Основные пропсы |
|-----------|----------|-----------------|
| `SbButton` | Кнопка с вариантами оформления | `variant`, `size`, `loading`, `disabled`, `icon` |
| `SbInput` | Текстовое поле с меткой и валидацией | `label`, `type`, `error`, `hint`, `disabled`, `required` |
| `SbToggle` | Переключатель | `label`, `disabled`, `size` (v-model: boolean) |
| `SbSlider` | Ползунок с форматированным значением | `min`, `max`, `step`, `label`, `showValue`, `formatValue` |
| `SbDropdown` | Триггер выпадающего меню | `align`, `width` |
| `SbDropdownItem` | Элемент выпадающего меню | `disabled`, `destructive` |

## Использование

Импортируйте компоненты из барреля `@/kit`:

```vue
<script setup lang="ts">
import { SbButton, SbBadge, SbCard } from '@/kit'
</script>

<template>
  <SbCard>
    <SbBadge tone="success" dot>Подключено</SbBadge>
    <SbButton variant="primary" @click="handleClick">
      Начать поток
    </SbButton>
  </SbCard>
</template>
```

Все компоненты автоматически поддерживают тёмную тему через систему дизайн-токенов — подробнее в [Дизайн-токены](./design-tokens/).
