<script setup lang="ts">
import { X, Search } from 'lucide-vue-next'

interface Filter {
  key: string
  label: string
  active?: boolean
}

withDefaults(
  defineProps<{
    filters?: Filter[]
    placeholder?: string
  }>(),
  {
    filters: () => [],
    placeholder: 'Search...',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  toggleFilter: [key: string]
}>()

const search = defineModel<string>({ default: '' })

function clearSearch() {
  search.value = ''
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <!-- Search input -->
    <div class="relative min-w-[200px] flex-1">
      <Search
        class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-secondary"
        aria-hidden="true"
      />
      <input
        v-model="search"
        type="text"
        role="searchbox"
        :placeholder="placeholder"
        class="w-full rounded-[--radius-input] border border-gray-200 bg-surface-card py-2 pl-9 pr-8 text-sm text-text-primary transition-colors placeholder:text-text-disabled focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary dark:border-gray-700 dark:bg-gray-800"
      />
      <button
        v-if="search"
        type="button"
        class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-secondary transition-colors hover:text-text-primary"
        aria-label="Clear search"
        @click="clearSearch"
      >
        <X class="h-4 w-4" />
      </button>
    </div>

    <!-- Filter chips -->
    <button
      v-for="filter in filters"
      :key="filter.key"
      type="button"
      class="cursor-pointer rounded-full px-3 py-1 text-sm transition-colors"
      :class="
        filter.active
          ? 'bg-primary text-white'
          : 'bg-surface-secondary text-text-secondary hover:bg-surface-secondary/80'
      "
      :aria-pressed="!!filter.active"
      @click="emit('toggleFilter', filter.key)"
    >
      {{ filter.label }}
    </button>
  </div>
</template>
