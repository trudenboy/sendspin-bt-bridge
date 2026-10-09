<script lang="ts">
const TAP_STEP = 5

/**
 * Where a tap on the bar leaves the volume: one step toward the tapped side.
 * Music Assistant's rule — a tap never jumps straight to the tapped level, so
 * a stray touch cannot blast a speaker at 100 %.
 */
export function tapStep(before: number, tapped: number, step = TAP_STEP): number {
  if (tapped === before) return before
  const next = tapped > before ? before + step : before - step
  return Math.max(0, Math.min(100, next))
}
</script>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { SbSlider } from '@/kit'
import { Volume2, VolumeX } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    mac: string
    volume: number
    muted: boolean
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  'update:volume': [value: number]
  'update:muted': [value: boolean]
}>()

const { t } = useI18n()

const localVolume = ref(props.volume)
let debounceTimer: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.volume,
  (v) => {
    localVolume.value = v
  },
)

function send(value: number) {
  localVolume.value = value
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => emit('update:volume', value), 300)
}

/* A touch that does not move is a tap: hold its native jump and step instead. */
const DRAG_PX = 8
let touch: { x: number; before: number; dragged: boolean; last: number | null } | null = null

function onPointerDown(e: PointerEvent) {
  if (e.pointerType !== 'touch') return
  touch = { x: e.clientX, before: localVolume.value, dragged: false, last: null }
}

function onPointerMove(e: PointerEvent) {
  if (touch && Math.abs(e.clientX - touch.x) > DRAG_PX) touch.dragged = true
}

function onPointerUp(e: PointerEvent) {
  if (!touch || e.pointerType !== 'touch') return
  const gesture = touch
  touch = null
  if (Math.abs(e.clientX - gesture.x) > DRAG_PX) gesture.dragged = true
  if (gesture.dragged) {
    if (gesture.last != null) send(gesture.last)
    return
  }
  // A tap is a deliberate single step: send it now, no debounce.
  if (debounceTimer) clearTimeout(debounceTimer)
  localVolume.value = tapStep(gesture.before, gesture.last ?? gesture.before)
  emit('update:volume', localVolume.value)
}

function onPointerCancel() {
  touch = null
}

function onVolumeInput(value: number) {
  if (touch) {
    touch.last = value
    if (touch.dragged) localVolume.value = value
    return
  }
  send(value)
}

function toggleMute() {
  emit('update:muted', !props.muted)
}
</script>

<template>
  <div class="flex items-center gap-2">
    <button
      type="button"
      class="shrink-0 cursor-pointer rounded p-1 text-text-secondary transition-colors hover:text-text-primary"
      :class="{ 'text-warning': muted }"
      :aria-label="muted ? t('volume.unmute') : t('volume.mute')"
      :disabled="disabled"
      @click="toggleMute"
    >
      <VolumeX v-if="muted" class="h-4 w-4" />
      <Volume2 v-else class="h-4 w-4" />
    </button>
    <SbSlider
      :model-value="localVolume"
      @pointerdown.capture="onPointerDown"
      @pointermove.capture="onPointerMove"
      @pointerup.capture="onPointerUp"
      @pointercancel.capture="onPointerCancel"
      :min="0"
      :max="100"
      :disabled="disabled || muted"
      :show-value="false"
      class="flex-1"
      @update:model-value="onVolumeInput"
    />
    <span class="w-8 shrink-0 text-right text-xs tabular-nums text-text-secondary">
      {{ localVolume }}%
    </span>
  </div>
</template>
