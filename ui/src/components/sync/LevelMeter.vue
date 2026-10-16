<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  /** The microphone level now, RMS 0..1. */
  read: () => number
}>()

/** Below this the clicks will likely not be found; above it the microphone may clip. (RMS: a click
 * track is mostly silence, so a level that only just clears room noise is not "fine".) */
const TOO_QUIET = 0.05
const TOO_LOUD = 0.7
const POLL_MS = 200

const { t } = useI18n()
const level = ref(0)
let timer: ReturnType<typeof setInterval> | null = null

function poll() {
  const v = props.read()
  level.value = Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0
}

onMounted(() => {
  poll()
  timer = setInterval(poll, POLL_MS)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})

const pct = computed(() => Math.round(level.value * 100))
const hint = computed(() =>
  level.value < TOO_QUIET ? 'tooQuiet' : level.value > TOO_LOUD ? 'tooLoud' : 'fine',
)
const barClass = computed(() =>
  hint.value === 'fine' ? 'bg-success' : hint.value === 'tooLoud' ? 'bg-error' : 'bg-warning',
)
</script>

<template>
  <div data-level-meter class="flex flex-col gap-2">
    <div class="flex items-center gap-3">
      <span class="w-24 shrink-0 text-sm font-medium text-text-primary" aria-hidden="true">{{ t('sync.wizard.level.meter') }}</span>
      <div
        role="meter"
        :aria-label="t('sync.wizard.level.meter')"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="pct"
        :aria-valuetext="t('sync.wizard.level.value', { pct })"
        class="h-3 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-secondary"
      >
        <div :class="['h-full rounded-full transition-[width] duration-150', barClass]" :style="{ width: `${pct}%` }" />
      </div>
      <span class="w-12 shrink-0 text-right text-sm tabular-nums text-text-primary">
        {{ t('sync.wizard.level.value', { pct }) }}
      </span>
    </div>
    <p :class="['text-sm', hint === 'fine' ? 'text-text-secondary' : 'text-text-primary']" :data-level-hint="hint">
      {{ t(`sync.wizard.level.${hint}`) }}
    </p>
  </div>
</template>
