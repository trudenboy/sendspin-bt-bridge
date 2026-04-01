<script setup lang="ts">
withDefaults(
  defineProps<{
    size?: 'sm' | 'md' | 'lg'
    label?: string
  }>(),
  { size: 'md', label: 'Loading' },
)

const sizeMap = { sm: 16, md: 24, lg: 40 } as const
</script>

<template>
  <span
    role="status"
    :aria-label="label"
    class="inline-flex items-center justify-center"
  >
    <svg
      :width="sizeMap[size]"
      :height="sizeMap[size]"
      viewBox="0 0 24 24"
      fill="none"
      class="animate-spin"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        stroke-width="3"
        stroke-linecap="round"
        class="opacity-25"
      />
      <path
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        stroke-width="3"
        stroke-linecap="round"
        class="opacity-75"
      />
    </svg>
    <span class="sr-only">{{ label }}</span>
  </span>
</template>
