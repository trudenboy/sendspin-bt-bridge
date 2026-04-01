<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown } from 'lucide-vue-next'

type Padding = 'none' | 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    collapsible?: boolean
    collapsed?: boolean
    loading?: boolean
    padding?: Padding
  }>(),
  {
    collapsible: false,
    collapsed: false,
    loading: false,
    padding: 'md',
  },
)

const emit = defineEmits<{
  'update:collapsed': [value: boolean]
}>()

const paddingClass = computed(() => {
  const map: Record<Padding, string> = {
    none: '',
    sm: 'p-2',
    md: 'p-4',
    lg: 'p-6',
  }
  return map[props.padding]
})

function toggleCollapsed() {
  if (props.collapsible) {
    emit('update:collapsed', !props.collapsed)
  }
}
</script>

<template>
  <div
    class="overflow-hidden rounded-[--radius-card] border border-gray-200 bg-surface-card shadow-sm dark:border-gray-700 dark:bg-gray-800"
  >
    <!-- Header -->
    <div
      v-if="$slots.header || $slots.actions"
      class="flex items-center justify-between border-b border-gray-200 px-4 py-3 font-semibold dark:border-gray-700"
    >
      <component
        :is="collapsible ? 'button' : 'div'"
        :type="collapsible ? 'button' : undefined"
        class="flex items-center gap-2"
        :class="{ 'cursor-pointer': collapsible }"
        :aria-expanded="collapsible ? !collapsed : undefined"
        @click="toggleCollapsed"
      >
        <ChevronDown
          v-if="collapsible"
          class="h-4 w-4 transition-transform duration-200"
          :class="{ '-rotate-90': collapsed }"
        />
        <slot name="header" />
      </component>
      <div v-if="$slots.actions">
        <slot name="actions" />
      </div>
    </div>

    <!-- Body -->
    <div v-show="!collapsible || !collapsed" class="relative">
      <!-- Loading overlay -->
      <div
        v-if="loading"
        class="absolute inset-0 z-10 flex items-center justify-center bg-surface-card/70 dark:bg-gray-800/70"
      >
        <svg
          class="h-6 w-6 animate-spin text-primary"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            class="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            stroke-width="4"
          />
          <path
            class="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          />
        </svg>
      </div>

      <div :class="paddingClass">
        <slot />
      </div>
    </div>

    <!-- Footer -->
    <div
      v-if="$slots.footer"
      class="border-t border-gray-200 bg-surface/50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/50"
    >
      <slot name="footer" />
    </div>
  </div>
</template>
