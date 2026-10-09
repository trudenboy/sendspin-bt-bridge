<script setup lang="ts">
import { computed, ref } from 'vue'

interface TimelineEvent {
  id: string
  timestamp: string
  title: string
  description?: string
  type?: 'info' | 'success' | 'warning' | 'error'
  icon?: string
}

const props = withDefaults(
  defineProps<{
    events: TimelineEvent[]
    maxItems?: number
  }>(),
  { maxItems: 0 },
)

const expanded = ref(false)

const visibleEvents = computed(() => {
  if (props.maxItems > 0 && !expanded.value) {
    return props.events.slice(0, props.maxItems)
  }
  return props.events
})

const hasMore = computed(
  () => props.maxItems > 0 && props.events.length > props.maxItems,
)

const dotColorMap = {
  info: 'bg-info',
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-error',
} as const
</script>

<template>
  <div role="list" class="relative">
    <!-- Vertical line -->
    <div
      class="absolute left-3 top-0 bottom-0 w-0.5 bg-gray-200 dark:bg-gray-700"
      aria-hidden="true"
    />

    <!-- Events -->
    <div
      v-for="event in visibleEvents"
      :key="event.id"
      role="listitem"
      class="relative flex pb-6 pl-8"
    >
      <!-- Dot -->
      <span
        :class="[
          'absolute left-0 h-6 w-6 rounded-full border-2 border-white dark:border-gray-800',
          dotColorMap[event.type ?? 'info'],
        ]"
        aria-hidden="true"
      >
        <span
          v-if="event.icon"
          class="flex h-full w-full items-center justify-center text-xs text-white"
        >
          {{ event.icon }}
        </span>
      </span>

      <!-- Content -->
      <div class="min-w-0">
        <span class="text-xs text-text-secondary">{{ event.timestamp }}</span>
        <p class="text-sm font-medium text-text-primary">{{ event.title }}</p>
        <p
          v-if="event.description"
          class="mt-1 text-sm text-text-secondary"
        >
          {{ event.description }}
        </p>
      </div>
    </div>

    <!-- Show more -->
    <button
      v-if="hasMore && !expanded"
      type="button"
      class="ml-8 cursor-pointer text-sm font-medium text-primary hover:text-primary-dark"
      data-testid="timeline-show-more"
      @click="expanded = true"
    >
      Show {{ events.length - maxItems }} more
    </button>
  </div>
</template>
