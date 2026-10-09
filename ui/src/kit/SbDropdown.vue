<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick, reactive } from 'vue'

interface Props {
  align?: 'left' | 'right'
  width?: 'auto' | 'full' | string
}

const props = withDefaults(defineProps<Props>(), {
  align: 'left',
  width: 'auto',
})

const isOpen = ref(false)
const containerRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
/* The menu is rendered on <body> with fixed coordinates, so tables and
   scrolling cards (overflow: auto/hidden) never clip it. */
const position = reactive<Record<string, string>>({})

function place() {
  const anchor = containerRef.value?.getBoundingClientRect()
  if (!anchor) return
  const menuHeight = menuRef.value?.offsetHeight ?? 0
  const below = window.innerHeight - anchor.bottom
  const up = menuHeight > 0 && below < menuHeight + 8 && anchor.top > below
  position.top = up ? '' : `${anchor.bottom + 4}px`
  position.bottom = up ? `${window.innerHeight - anchor.top + 4}px` : ''
  position.left = props.align === 'right' ? '' : `${anchor.left}px`
  position.right = props.align === 'right' ? `${window.innerWidth - anchor.right}px` : ''
  position.width = props.width === 'full' ? `${anchor.width}px` : props.width === 'auto' ? '' : props.width
}

function toggle() {
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    place()
    nextTick(() => {
      place()
      focusFirstItem()
    })
  }
}

function close() {
  isOpen.value = false
}

function onViewportChange() {
  if (isOpen.value) close()
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
  const target = e.target as Node
  if (containerRef.value?.contains(target) || menuRef.value?.contains(target)) return
  close()
}

onMounted(() => {
  document.addEventListener('click', onClickOutside)
  window.addEventListener('resize', onViewportChange)
  window.addEventListener('scroll', onViewportChange, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onClickOutside)
  window.removeEventListener('resize', onViewportChange)
  window.removeEventListener('scroll', onViewportChange, true)
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
          class="inline-flex items-center gap-1 rounded-(--radius-button) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-secondary"
        >
          Menu
          <svg class="h-4 w-4 transition-transform" :class="[isOpen ? 'rotate-180' : '']" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
          </svg>
        </button>
      </slot>
    </div>

    <!-- Menu -->
    <Teleport to="body">
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
          class="fixed z-[60] max-h-[60vh] overflow-y-auto rounded-(--radius-card) border border-border bg-surface-raised py-1 shadow-lg"
          :class="[width === 'auto' ? 'min-w-[12rem]' : '']"
          :style="position"
          @click="close"
          @keydown="onKeydown"
        >
          <slot />
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
