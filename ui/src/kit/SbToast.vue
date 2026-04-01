<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from 'vue'

const props = withDefaults(
  defineProps<{
    id: number
    message: string
    type?: 'success' | 'error' | 'warning' | 'info'
    duration?: number
    closable?: boolean
  }>(),
  { type: 'info', duration: 5000, closable: true },
)

const emit = defineEmits<{
  close: [id: number]
}>()

let timer: ReturnType<typeof setTimeout> | null = null

const visible = ref(true)

const typeStyles = computed(() => {
  const map = {
    success: 'border-l-4 border-success bg-green-50 dark:bg-green-900/20',
    error: 'border-l-4 border-error bg-red-50 dark:bg-red-900/20',
    warning: 'border-l-4 border-warning bg-amber-50 dark:bg-amber-900/20',
    info: 'border-l-4 border-info bg-blue-50 dark:bg-blue-900/20',
  } as const
  return map[props.type]
})

const iconMap = {
  success: '✓',
  error: '✕',
  warning: '⚠',
  info: 'ℹ',
} as const

function dismiss() {
  visible.value = false
  emit('close', props.id)
}

onMounted(() => {
  if (props.duration > 0) {
    timer = setTimeout(dismiss, props.duration)
  }
})

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div
    v-if="visible"
    role="alert"
    :class="[
      'flex min-w-[300px] max-w-[420px] items-center gap-3 rounded-[--radius-card] px-4 py-3 shadow-lg',
      typeStyles,
    ]"
    :data-toast-id="id"
  >
    <!-- Icon -->
    <span class="shrink-0 text-lg" aria-hidden="true">
      {{ iconMap[type] }}
    </span>

    <!-- Message -->
    <p class="flex-1 text-sm text-text-primary">{{ message }}</p>

    <!-- Close button -->
    <button
      v-if="closable"
      type="button"
      class="shrink-0 cursor-pointer text-text-secondary transition-colors hover:text-text-primary"
      aria-label="Dismiss notification"
      data-testid="toast-close-btn"
      @click="dismiss"
    >
      <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
      </svg>
    </button>
  </div>
</template>
