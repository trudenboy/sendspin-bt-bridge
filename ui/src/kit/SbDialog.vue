<script setup lang="ts">
import { computed, watch, onBeforeUnmount, nextTick, ref, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue?: boolean
    title?: string
    size?: 'sm' | 'md' | 'lg' | 'xl'
    closable?: boolean
    persistent?: boolean
  }>(),
  { modelValue: false, size: 'md', closable: true, persistent: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  close: []
}>()

const open = defineModel<boolean>({ default: false })

const titleId = useId()
const panelRef = ref<HTMLElement | null>(null)
const triggerElement = ref<Element | null>(null)

const sizeClass = computed(() => {
  const map = { sm: 'max-w-sm', md: 'max-w-md', lg: 'max-w-lg', xl: 'max-w-xl' } as const
  return map[props.size]
})

function close() {
  if (!props.closable) return
  open.value = false
  emit('close')
}

function onBackdropClick() {
  if (props.persistent) return
  close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && !props.persistent) {
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
    <Transition name="sb-dialog">
      <div
        v-if="open"
        class="fixed inset-0 z-40 flex items-center justify-center"
      >
        <!-- Backdrop -->
        <div
          class="absolute inset-0 bg-black/50 transition-opacity"
          data-testid="dialog-backdrop"
          @click="onBackdropClick"
        />

        <!-- Panel -->
        <div
          ref="panelRef"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="title ? titleId : undefined"
          :class="[
            'relative z-50 flex max-h-[85vh] w-full flex-col overflow-y-auto rounded-[--radius-card] bg-surface-card shadow-xl dark:bg-gray-800',
            sizeClass,
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
              aria-label="Close dialog"
              data-testid="dialog-close-btn"
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
            class="flex shrink-0 justify-end gap-3 border-t px-6 py-4"
          >
            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sb-dialog-enter-active,
.sb-dialog-leave-active {
  transition: opacity 0.2s ease;
}
.sb-dialog-enter-active [role="dialog"],
.sb-dialog-leave-active [role="dialog"] {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.sb-dialog-enter-from,
.sb-dialog-leave-to {
  opacity: 0;
}
.sb-dialog-enter-from [role="dialog"] {
  opacity: 0;
  transform: scale(0.95);
}
.sb-dialog-leave-to [role="dialog"] {
  opacity: 0;
  transform: scale(0.95);
}
</style>
