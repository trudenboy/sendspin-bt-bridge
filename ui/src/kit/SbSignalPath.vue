<script setup lang="ts">
import { computed } from 'vue'

interface Segment {
  id: string
  label: string
  status?: 'active' | 'inactive' | 'error'
  sublabel?: string
}

const props = withDefaults(
  defineProps<{
    segments: Segment[]
    direction?: 'horizontal' | 'vertical'
  }>(),
  { direction: 'horizontal' },
)

const statusStyles = {
  active: 'border-success bg-green-50 dark:bg-green-900/10',
  inactive: 'border-gray-300 bg-surface-secondary dark:bg-gray-800',
  error: 'border-error bg-red-50 dark:bg-red-900/10',
} as const

const isVertical = computed(() => props.direction === 'vertical')
</script>

<template>
  <div
    aria-label="Signal path"
    :class="[
      'flex flex-wrap items-center gap-1',
      isVertical ? 'flex-col' : 'flex-row max-sm:flex-col',
    ]"
  >
    <template v-for="(seg, i) in segments" :key="seg.id">
      <!-- Segment box -->
      <div
        :class="[
          'rounded-lg border-2 px-3 py-2 text-sm',
          statusStyles[seg.status ?? 'inactive'],
        ]"
        :aria-label="`${seg.label}${seg.sublabel ? ': ' + seg.sublabel : ''} — ${seg.status ?? 'inactive'}`"
      >
        <span class="font-medium text-text-primary">{{ seg.label }}</span>
        <span
          v-if="seg.sublabel"
          class="block text-xs text-text-secondary"
        >
          {{ seg.sublabel }}
        </span>
      </div>

      <!-- Arrow connector -->
      <span
        v-if="i < segments.length - 1"
        :class="[
          'select-none text-text-secondary',
          isVertical ? 'my-1' : 'mx-2 max-sm:mx-0 max-sm:my-1',
        ]"
        aria-hidden="true"
      >
        <template v-if="isVertical">↓</template>
        <template v-else>
          <span class="max-sm:hidden">→</span>
          <span class="hidden max-sm:inline">↓</span>
        </template>
      </span>
    </template>
  </div>
</template>
