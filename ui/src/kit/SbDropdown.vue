<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'

interface Props {
  align?: 'left' | 'right'
  width?: 'auto' | 'full' | string
}

withDefaults(defineProps<Props>(), {
  align: 'left',
  width: 'auto',
})

const isOpen = ref(false)
const containerRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)

function toggle() {
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    nextTick(() => focusFirstItem())
  }
}

function close() {
  isOpen.value = false
}

function focusFirstItem() {
  const items = menuRef.value?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])')
  items?.[0]?.focus()
}

function onKeydown(e: KeyboardEvent) {
  if (!isOpen.value) return

  if (e.key === 'Escape') {
    e.preventDefault()
    close()
    return
  }

  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    const items = Array.from(
      menuRef.value?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []
    )
    if (items.length === 0) return

    const currentIndex = items.indexOf(document.activeElement as HTMLElement)
    let nextIndex: number

    if (e.key === 'ArrowDown') {
      nextIndex = currentIndex < items.length - 1 ? currentIndex + 1 : 0
    } else {
      nextIndex = currentIndex > 0 ? currentIndex - 1 : items.length - 1
    }

    items[nextIndex]?.focus()
  }
}

function onClickOutside(e: MouseEvent) {
  if (containerRef.value && !containerRef.value.contains(e.target as Node)) {
    close()
  }
}

onMounted(() => {
  document.addEventListener('click', onClickOutside)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onClickOutside)
})
</script>

<template>
  <div
    ref="containerRef"
    class="relative inline-block"
    :class="[width === 'full' ? 'w-full' : '']"
    @keydown="onKeydown"
  >
    <!-- Trigger -->
    <div @click="toggle">
      <slot name="trigger">
        <button
          type="button"
          aria-haspopup="true"
          :aria-expanded="isOpen"
          class="inline-flex items-center gap-1 rounded-[--radius-button] border border-gray-300 bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-secondary dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
        >
          Menu
          <svg class="h-4 w-4 transition-transform" :class="[isOpen ? 'rotate-180' : '']" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
          </svg>
        </button>
      </slot>
    </div>

    <!-- Menu -->
    <Transition
      enter-active-class="transition duration-100 ease-out"
      enter-from-class="scale-95 opacity-0"
      enter-to-class="scale-100 opacity-100"
      leave-active-class="transition duration-75 ease-in"
      leave-from-class="scale-100 opacity-100"
      leave-to-class="scale-95 opacity-0"
    >
      <div
        v-if="isOpen"
        ref="menuRef"
        role="menu"
        class="absolute z-50 mt-1 overflow-hidden rounded-[--radius-card] border border-gray-200 bg-surface-card py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
        :class="[
          align === 'right' ? 'right-0' : 'left-0',
          width === 'full' ? 'w-full' : width === 'auto' ? 'min-w-[12rem]' : '',
        ]"
        :style="width !== 'auto' && width !== 'full' ? { width } : undefined"
        @click="close"
      >
        <slot />
      </div>
    </Transition>
  </div>
</template>
