---
title: Дизайн-токены
description: Цветовая палитра, типографика, отступы и токены тёмной темы
---

## Обзор

UI Kit использует **Tailwind CSS v4** токены, определённые в CSS-блоке `@theme {}`, а не в JavaScript-конфигурации. Все токены хранятся в одном файле:

📄 **Файл:** `ui/src/app.css`

## Цветовая палитра

Цвета определены как CSS-переменные внутри `@theme {}`:

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

| Токен | Цвет | Назначение |
|-------|------|------------|
| `primary` | Синий (`#03a9f4`) | Кнопки, ссылки, активные состояния |
| `accent` | Оранжевый (`#ff9800`) | Акценты, вторичные действия |
| `success` | Зелёный (`#43a047`) | Состояние подключения, подтверждения |
| `warning` | Оранжевый (`#ffa600`) | Деградированные состояния, предупреждения |
| `error` | Красный (`#db4437`) | Ошибки, деструктивные действия |
| `info` | Синий (`#039be5`) | Информационные бейджи, подсказки |

### Цвета поверхностей и текста

```css
@theme {
  /* Поверхности — светлая тема */
  --color-surface: #fafafa;
  --color-surface-secondary: #e5e5e5;
  --color-surface-card: #ffffff;

  /* Текст — светлая тема */
  --color-text-primary: #212121;
  --color-text-secondary: #727272;
  --color-text-disabled: #bdbdbd;

  /* Блоки кода */
  --color-code-bg: #1e293b;
  --color-code-text: #e2e8f0;
}
```

## Тёмная тема

Тёмная тема использует директиву `@custom-variant` из Tailwind v4:

```css
@custom-variant dark (&:where(.dark, .dark *));
```

Активируется при наличии класса `.dark` на элементе-предке. Все утилиты `dark:` в Tailwind применяются автоматически — дополнительный JavaScript не требуется.

## Тоновые утилиты

Утилиты `tone-*` используются компонентом `SbBadge` и другими status-aware компонентами для применения согласованного семантического фона и цвета текста:

| Утилита | Фон | Цвет текста |
|---------|-----|-------------|
| `tone-success` | Зелёный с 12% непрозрачности | `--color-success` |
| `tone-warning` | Оранжевый с 12% непрозрачности | `--color-warning` |
| `tone-error` | Красный с 12% непрозрачности | `--color-error` |
| `tone-info` | Синий с 12% непрозрачности | `--color-info` |
| `tone-neutral` | Серый с 10% непрозрачности | `--color-text-secondary` |

Пример использования:

```vue
<SbBadge tone="success" dot>Вещание</SbBadge>
<SbBadge tone="error">Офлайн</SbBadge>
```

## Типографика

Стек шрифтов использует системные шрифты для оптимального рендеринга:

```css
@theme {
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI',
               Helvetica, Arial, sans-serif;
  --font-mono: 'SF Mono', SFMono-Regular, ui-monospace,
               Menlo, Consolas, monospace;
}
```

## Скругления

Токены радиусов обеспечивают единообразное скругление во всём интерфейсе:

```css
@theme {
  --radius-card: 12px;
  --radius-badge: 999px;
  --radius-button: 8px;
  --radius-input: 8px;
}
```

| Токен | Значение | Назначение |
|-------|----------|------------|
| `--radius-card` | `12px` | Карточки, диалоги, панели |
| `--radius-badge` | `999px` | Бейджи (форма пилюли) |
| `--radius-button` | `8px` | Кнопки |
| `--radius-input` | `8px` | Поля ввода, селекты, переключатели |
