<script setup lang="ts">
import { ref, computed } from 'vue'

interface Tab {
  id: string
  label: string
  icon?: string
  badge?: string | number
  disabled?: boolean
}

const props = defineProps<{
  tabs: Tab[]
}>()

defineEmits<{
  'update:modelValue': [value: string]
}>()

const activeTab = defineModel<string>()

const currentTab = computed(() => {
  return activeTab.value || props.tabs[0]?.id || ''
})

const tabListRef = ref<HTMLElement | null>(null)

function selectTab(tab: Tab) {
  if (tab.disabled) return
  activeTab.value = tab.id
}

function onKeydown(event: KeyboardEvent) {
  const enabledTabs = props.tabs.filter((t) => !t.disabled)
  const currentIndex = enabledTabs.findIndex((t) => t.id === currentTab.value)
  let nextIndex = currentIndex

  switch (event.key) {
    case 'ArrowRight':
      event.preventDefault()
      nextIndex = (currentIndex + 1) % enabledTabs.length
      break
    case 'ArrowLeft':
      event.preventDefault()
      nextIndex =
        (currentIndex - 1 + enabledTabs.length) % enabledTabs.length
      break
    case 'Home':
      event.preventDefault()
      nextIndex = 0
      break
    case 'End':
      event.preventDefault()
      nextIndex = enabledTabs.length - 1
      break
    default:
      return
  }

  const nextTab = enabledTabs[nextIndex]
  if (nextTab) {
    activeTab.value = nextTab.id
    const btn = tabListRef.value?.querySelector<HTMLElement>(
      `[data-tab-id="${nextTab.id}"]`,
    )
    btn?.focus()
  }
}
</script>

<template>
  <div>
    <!-- Tab bar -->
    <div
      ref="tabListRef"
      role="tablist"
      class="flex overflow-x-auto border-b border-gray-200 dark:border-gray-700"
      @keydown="onKeydown"
    >
      <button
        v-for="tab in tabs"
        :key="tab.id"
        role="tab"
        type="button"
        :id="`tab-${tab.id}`"
        :data-tab-id="tab.id"
        :aria-selected="currentTab === tab.id"
        :aria-controls="`panel-${tab.id}`"
        :tabindex="currentTab === tab.id ? 0 : -1"
        :disabled="tab.disabled"
        class="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-4 py-2 text-sm transition-colors"
        :class="[
          currentTab === tab.id
            ? 'border-primary font-medium text-primary'
            : 'border-transparent text-text-secondary hover:bg-surface-secondary/50',
          tab.disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
        ]"
        @click="selectTab(tab)"
      >
        <span>{{ tab.label }}</span>
        <span
          v-if="tab.badge != null"
          class="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary/10 px-1.5 text-xs font-medium text-primary"
        >
          {{ tab.badge }}
        </span>
      </button>
    </div>

    <!-- Tab panels -->
    <div
      v-for="tab in tabs"
      :key="tab.id"
      v-show="currentTab === tab.id"
      :id="`panel-${tab.id}`"
      role="tabpanel"
      :aria-labelledby="`tab-${tab.id}`"
      :tabindex="0"
    >
      <slot :name="tab.id" />
    </div>
  </div>
</template>
