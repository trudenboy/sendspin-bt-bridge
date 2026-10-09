<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    status: 'streaming' | 'ready' | 'connecting' | 'error' | 'offline' | 'standby'
    pulse?: boolean | null
    size?: 'sm' | 'md'
  }>(),
  { size: 'md', pulse: null },
)

const colorMap = {
  streaming: 'bg-success',
  ready: 'bg-info',
  connecting: 'bg-warning',
  error: 'bg-error',
  offline: 'bg-gray-400',
  standby: 'bg-text-disabled',
} as const

const shouldPulse = computed(
  () => props.pulse ?? (props.status === 'streaming' || props.status === 'connecting'),
)

const sizeClass = computed(() => (props.size === 'sm' ? 'h-2 w-2' : 'h-3 w-3'))

const statusLabel = computed(() => {
  const labels: Record<string, string> = {
    streaming: 'Streaming',
    ready: 'Ready',
    connecting: 'Connecting',
    error: 'Error',
    offline: 'Offline',
    standby: 'Standby',
  }
  return labels[props.status]
})
</script>

<template>
  <span
    class="relative inline-flex"
    role="status"
    :aria-label="statusLabel"
  >
    <span
      :class="[
        'inline-block rounded-full',
        colorMap[status],
        sizeClass,
      ]"
    />
    <span
      v-if="shouldPulse"
      :class="[
        'absolute inset-0 inline-block animate-ping rounded-full opacity-75',
        colorMap[status],
      ]"
    />
  </span>
</template>
