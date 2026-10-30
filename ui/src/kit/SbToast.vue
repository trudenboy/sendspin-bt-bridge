<script setup lang="ts">
import { useKitLabels } from './labels'
import { onMounted, onBeforeUnmount, ref, computed } from 'vue'

const labels = useKitLabels()

const props = withDefaults(
  defineProps<{
    id: number
    message: string
    type?: 'success' | 'error' | 'warning' | 'info'
    duration?: number
    closable?: boolean
    actionLabel?: string
  }>(),
  { type: 'info', duration: 5000, closable: true },
)

const emit = defineEmits<{
  close: [id: number]
  action: [id: number]
}>()

let timer: ReturnType<typeof setTimeout> | null = null

const visible = ref(true)

const typeStyles = computed(() => {
  const map = {
    success: 'border-l-4 border-success bg-success/10',
    error: 'border-l-4 border-error bg-error/10',
    warning: 'border-l-4 border-warning bg-warning/10',
    info: 'border-l-4 border-info bg-info/10',
  } as const
  return map[props.type]
})

const iconMap = {
  success: '✓',
  error: '✕',
  warning: '⚠',
  info: 'ℹ',
} as const

function onAction() {
  emit('action', props.id)
  dismiss()
}

function dismiss() {
  visible.value = false
  emit('close', props.id)
}

function start() {
  stop()
  if (props.duration > 0) timer = setTimeout(dismiss, props.duration)
}
function stop() {
  if (timer) clearTimeout(timer)
  timer = null
}

onMounted(start)

onBeforeUnmount(stop)
</script>

<template>
  <div
    v-if="visible"
    :role="type === 'error' ? 'alert' : 'status'"
    :class="[
      'flex min-w-[300px] max-w-[420px] items-center gap-3 rounded-(--radius-card) px-4 py-3 shadow-lg',
      typeStyles,
    ]"
    :data-toast-id="id"
    @mouseenter="stop"
    @mouseleave="start"
    @focusin="stop"
    @focusout="start"
  >
    <!-- Icon -->
    <span class="shrink-0 text-lg" aria-hidden="true">
      {{ iconMap[type] }}
    </span>

    <!-- Message -->
    <p class="flex-1 text-sm text-text-primary">{{ message }}</p>

    <button
      v-if="actionLabel"
      type="button"
      class="shrink-0 cursor-pointer rounded-(--radius-button) px-2 py-1 text-sm font-medium text-primary-text underline-offset-2 hover:underline"
      data-testid="toast-action-btn"
      @click="onAction"
    >
      {{ actionLabel }}
    </button>

    <!-- Close button -->
    <button
      v-if="closable"
      type="button"
      class="-my-3 -mr-3 inline-flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-full text-text-secondary transition-colors hover:text-text-primary"
      :aria-label="labels.dismissNotification"
      data-testid="toast-close-btn"
      @click="dismiss"
    >
      <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
      </svg>
    </button>
  </div>
</template>
