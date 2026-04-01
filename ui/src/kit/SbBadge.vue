<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'success' | 'warning' | 'error' | 'info' | 'neutral'
    size?: 'sm' | 'md'
    dot?: boolean
    removable?: boolean
  }>(),
  { tone: 'neutral', size: 'md', dot: false, removable: false },
)

const emit = defineEmits<{
  remove: []
}>()

const toneClass = {
  success: 'tone-success',
  warning: 'tone-warning',
  error: 'tone-error',
  info: 'tone-info',
  neutral: 'tone-neutral',
} as const

const dotColor = {
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-error',
  info: 'bg-info',
  neutral: 'bg-gray-400',
} as const
</script>

<template>
  <span
    :class="[
      'inline-flex items-center gap-1 rounded-[--radius-badge] font-medium',
      toneClass[tone],
      size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm',
    ]"
  >
    <span
      v-if="dot"
      :class="['inline-block h-1.5 w-1.5 rounded-full', dotColor[tone]]"
      aria-hidden="true"
    />
    <slot />
    <button
      v-if="removable"
      type="button"
      class="-mr-0.5 ml-0.5 inline-flex items-center justify-center rounded-full p-0.5 opacity-60 transition-opacity hover:opacity-100 focus:outline-none"
      aria-label="Remove"
      @click="emit('remove')"
    >
      <svg class="h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M3 3l6 6M9 3l-6 6" />
      </svg>
    </button>
  </span>
</template>
