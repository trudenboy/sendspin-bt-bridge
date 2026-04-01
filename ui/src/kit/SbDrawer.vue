<script setup lang="ts">
import { computed, watch, onBeforeUnmount, nextTick, ref, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue?: boolean
    title?: string
    side?: 'left' | 'right'
    width?: string
    closable?: boolean
  }>(),
  { modelValue: false, side: 'right', width: 'max-w-md', closable: true },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  close: []
}>()

const open = defineModel<boolean>({ default: false })

const titleId = useId()
const panelRef = ref<HTMLElement | null>(null)
const triggerElement = ref<Element | null>(null)

const sideClasses = computed(() =>
  props.side === 'left' ? 'left-0' : 'right-0',
)

const transitionName = computed(() =>
  props.side === 'left' ? 'sb-drawer-left' : 'sb-drawer-right',
)

function close() {
  if (!props.closable) return
  open.value = false
  emit('close')
}

function onBackdropClick() {
  close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    close()
    return
  }
  if (e.key === 'Tab') {
    trapFocus(e)
  }
}

function trapFocus(e: KeyboardEvent) {
  const panel = panelRef.value
  if (!panel) return

  const focusables = panel.querySelectorAll<HTMLElement>(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  )
  if (focusables.length === 0) return

  const first = focusables[0]
  const last = focusables[focusables.length - 1]

  if (e.shiftKey) {
    if (document.activeElement === first) {
      e.preventDefault()
      last.focus()
    }
  } else {
    if (document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }
}

function focusFirstElement() {
  const panel = panelRef.value
  if (!panel) return
  const focusable = panel.querySelector<HTMLElement>(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  )
  focusable?.focus()
}

watch(open, async (isOpen) => {
  if (isOpen) {
    triggerElement.value = document.activeElement
    document.body.classList.add('overflow-hidden')
    document.addEventListener('keydown', onKeydown)
    await nextTick()
    focusFirstElement()
  } else {
    document.body.classList.remove('overflow-hidden')
    document.removeEventListener('keydown', onKeydown)
    if (triggerElement.value instanceof HTMLElement) {
      triggerElement.value.focus()
    }
  }
}, { immediate: true })

onBeforeUnmount(() => {
  document.body.classList.remove('overflow-hidden')
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-40">
      <!-- Backdrop -->
      <Transition name="sb-fade">
        <div
          v-if="open"
          class="absolute inset-0 bg-black/30"
          data-testid="drawer-backdrop"
          @click="onBackdropClick"
        />
      </Transition>

      <!-- Panel -->
      <Transition :name="transitionName">
        <div
          v-if="open"
          ref="panelRef"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="title ? titleId : undefined"
          :class="[
            'fixed top-0 bottom-0 z-50 flex w-full flex-col bg-surface-card shadow-xl dark:bg-gray-800',
            sideClasses,
            width,
          ]"
        >
          <!-- Header -->
          <div
            v-if="title || $slots.header"
            class="flex shrink-0 items-center justify-between border-b px-6 py-4"
          >
            <slot name="header">
              <h2
                :id="titleId"
                class="text-lg font-semibold text-text-primary"
              >
                {{ title }}
              </h2>
            </slot>
            <button
              v-if="closable"
              type="button"
              class="ml-4 cursor-pointer text-text-secondary transition-colors hover:text-text-primary"
              aria-label="Close drawer"
              data-testid="drawer-close-btn"
              @click="close"
            >
              <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
              </svg>
            </button>
          </div>

          <!-- Body -->
          <div class="flex-1 overflow-y-auto px-6 py-4">
            <slot />
          </div>

          <!-- Footer -->
          <div
            v-if="$slots.footer"
            class="shrink-0 border-t px-6 py-4"
          >
            <slot name="footer" />
          </div>
        </div>
      </Transition>
    </div>
  </Teleport>
</template>

<style scoped>
/* Fade for backdrop */
.sb-fade-enter-active,
.sb-fade-leave-active {
  transition: opacity 0.2s ease;
}
.sb-fade-enter-from,
.sb-fade-leave-to {
  opacity: 0;
}

/* Slide from right */
.sb-drawer-right-enter-active,
.sb-drawer-right-leave-active {
  transition: transform 0.3s ease;
}
.sb-drawer-right-enter-from,
.sb-drawer-right-leave-to {
  transform: translateX(100%);
}

/* Slide from left */
.sb-drawer-left-enter-active,
.sb-drawer-left-leave-active {
  transition: transform 0.3s ease;
}
.sb-drawer-left-enter-from,
.sb-drawer-left-leave-to {
  transform: translateX(-100%);
}
</style>
