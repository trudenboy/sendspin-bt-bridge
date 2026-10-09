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

function onVolumeInput(value: number) {
  localVolume.value = value
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    emit('update:volume', value)
  }, 300)
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
