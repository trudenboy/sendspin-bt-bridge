<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbButton, SbSpinner } from '@/kit'
import { AlertTriangle, CheckCircle2 } from 'lucide-vue-next'

/**
 * The one way a long operation (Bluetooth scan, pairing, update, restart,
 * measurement) shows itself: what runs, how long it has run, progress when it
 * is known, Cancel where the backend supports it, and on failure the reason with
 * the next step. See kit/POLICY.md.
 */
const props = withDefaults(
  defineProps<{
    label: string
    status?: 'running' | 'done' | 'error'
    /** Epoch ms; the elapsed seconds count from here (from mount when absent). */
    startedAt?: number | null
    /** 0 to 1 when known; omitted for an open-ended wait. */
    progress?: number | null
    cancellable?: boolean
    cancelling?: boolean
    /** Show Try again on failure (the parent handles `retry`). */
    retryable?: boolean
    error?: string | null
    /** What to do about the failure. */
    nextStep?: string | null
    /** `banner` is a full-width strip (restart, update); `inline` sits in a dialog or page. */
    variant?: 'inline' | 'banner'
    /** Hide the seconds counter. */
    hideElapsed?: boolean
  }>(),
  { status: 'running', startedAt: null, progress: null, variant: 'inline' },
)

const emit = defineEmits<{ cancel: []; retry: [] }>()
const { t } = useI18n()

const mountedAt = Date.now()
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

function stop() {
  if (timer) clearInterval(timer)
  timer = null
}
function start() {
  stop()
  now.value = Date.now()
  if (props.status === 'running') timer = setInterval(() => (now.value = Date.now()), 1000)
}
onMounted(start)
watch(() => props.status, start)
onBeforeUnmount(stop)

const elapsed = computed(() => Math.max(0, Math.floor((now.value - (props.startedAt ?? mountedAt)) / 1000)))
const percent = computed(() => (props.progress == null ? null : Math.round(Math.min(1, Math.max(0, props.progress)) * 100)))

const tone = computed(() => {
  if (props.status === 'error') return props.variant === 'banner' ? 'bg-warning/10 border-warning/20 text-warning-text' : 'border-error/50 bg-error/10 text-text-primary'
  if (props.status === 'done') return props.variant === 'banner' ? 'bg-success/10 border-success/20 text-success' : 'border-success/40 bg-success/10 text-text-primary'
  return props.variant === 'banner' ? 'bg-info/10 border-info/20 text-info' : 'border-border bg-surface-secondary text-text-primary'
})
const layout = computed(() =>
  props.variant === 'banner'
    ? 'flex flex-wrap items-center justify-center gap-3 border-b px-4 py-2.5'
    : 'flex flex-col gap-2 rounded-(--radius-card) border px-4 py-3 text-sm',
)
</script>

<template>
  <div :role="status === 'error' && variant === 'inline' ? 'alert' : 'status'" :class="[layout, tone]" data-testid="longop" :data-status="status">
    <div class="flex flex-wrap items-center gap-3" :class="variant === 'banner' ? 'justify-center' : ''">
      <SbSpinner v-if="status === 'running'" size="sm" />
      <AlertTriangle v-else-if="status === 'error'" class="size-4 shrink-0" aria-hidden="true" />
      <CheckCircle2 v-else class="size-4 shrink-0" aria-hidden="true" />
      <span class="text-sm font-medium">{{ status === 'error' && error ? error : label }}</span>
      <span v-if="status === 'running' && !hideElapsed" class="text-sm opacity-70" data-testid="longop-elapsed">{{ t('longOp.elapsed', { seconds: elapsed }) }}</span>
      <SbButton
        v-if="status === 'running' && cancellable"
        type="button"
        size="sm"
        variant="ghost"
        :disabled="cancelling"
        data-testid="longop-cancel"
        @click="emit('cancel')"
      >
        {{ cancelling ? t('longOp.cancelling') : t('longOp.cancel') }}
      </SbButton>
    </div>
    <div
      v-if="status === 'running' && percent != null"
      role="progressbar"
      :aria-valuenow="percent"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="label"
      class="h-1.5 w-full overflow-hidden rounded-full bg-border"
    >
      <div class="h-full bg-primary transition-all" :style="{ width: `${percent}%` }" />
    </div>
    <template v-if="status === 'error'">
      <p v-if="nextStep" class="text-sm opacity-90">{{ nextStep }}</p>
      <div class="flex flex-wrap items-center gap-2">
        <SbButton v-if="retryable" type="button" size="sm" variant="outline" data-testid="longop-retry" @click="emit('retry')">
          {{ t('longOp.retry') }}
        </SbButton>
        <slot name="actions" />
      </div>
    </template>
  </div>
</template>
