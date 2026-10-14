<script setup lang="ts">
import { toRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePlaybackProgress } from '@/composables/usePlaybackProgress'
import type { Device } from '@/api/types'

const props = defineProps<{
  device: Device
  slim?: boolean
  /** Click or arrow keys move playback (the player can seek). */
  seekable?: boolean
  /** A command is on its way: no seeking until it lands. */
  disabled?: boolean
}>()

const emit = defineEmits<{ seek: [positionMs: number] }>()

const { t } = useI18n()
const deviceRef = toRef(props, 'device')
const { progressPct, elapsed, duration, elapsedText, durationText, hasProgress } =
  usePlaybackProgress(deviceRef)

/** Arrow keys step this many seconds. */
const STEP_S = 5

function seekTo(seconds: number) {
  if (props.disabled || duration.value <= 0) return
  const clamped = Math.min(Math.max(seconds, 0), duration.value)
  emit('seek', Math.round(clamped * 1000))
}

function onClick(e: MouseEvent) {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  if (rect.width <= 0) return
  seekTo(((e.clientX - rect.left) / rect.width) * duration.value)
}

function onKeydown(e: KeyboardEvent) {
  const step = { ArrowRight: STEP_S, ArrowUp: STEP_S, ArrowLeft: -STEP_S, ArrowDown: -STEP_S }[e.key]
  if (step !== undefined) seekTo(elapsed.value + step)
  else if (e.key === 'Home') seekTo(0)
  else return
  e.preventDefault()
}
</script>

<template>
  <div v-if="hasProgress" class="flex flex-col gap-0.5">
    <!-- Seekable: a taller hit area around the thin bar -->
    <div
      v-if="seekable && !slim"
      data-seek
      role="slider"
      tabindex="0"
      class="group -my-1.5 cursor-pointer rounded-full py-1.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      :class="disabled ? 'cursor-wait opacity-60' : ''"
      :aria-label="t('transport.position')"
      aria-valuemin="0"
      :aria-valuemax="Math.round(duration)"
      :aria-valuenow="Math.round(elapsed)"
      :aria-valuetext="`${elapsedText} / ${durationText}`"
      :aria-disabled="disabled ? 'true' : undefined"
      @click="onClick"
      @keydown="onKeydown"
    >
      <div
        class="h-1 w-full overflow-hidden rounded-full transition-[height] group-hover:h-1.5"
        :style="{ backgroundColor: 'var(--color-surface-secondary, #e5e7eb)' }"
      >
        <div
          class="h-full rounded-full bg-primary transition-[width] duration-1000 ease-linear"
          :style="{ width: `${progressPct}%` }"
        />
      </div>
    </div>
    <!-- Progress bar -->
    <div
      v-else
      class="w-full overflow-hidden rounded-full"
      :class="slim ? 'h-0.5' : 'h-1'"
      :style="{ backgroundColor: 'var(--color-surface-secondary, #e5e7eb)' }"
    >
      <div
        class="h-full rounded-full bg-primary transition-[width] duration-1000 ease-linear"
        :style="{ width: `${progressPct}%` }"
      />
    </div>
    <!-- Time labels (hidden in slim mode) -->
    <div v-if="!slim" class="flex justify-end">
      <span class="text-xs tabular-nums text-text-secondary">
        {{ elapsedText }} / {{ durationText }}
      </span>
    </div>
  </div>
</template>
