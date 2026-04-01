<script setup lang="ts">
import { toRef } from 'vue'
import { usePlaybackProgress } from '@/composables/usePlaybackProgress'
import type { DeviceSnapshot } from '@/api/types'

const props = defineProps<{
  device: DeviceSnapshot
  slim?: boolean
}>()

const deviceRef = toRef(props, 'device')
const { progressPct, elapsedText, durationText, hasProgress } =
  usePlaybackProgress(deviceRef)
</script>

<template>
  <div v-if="hasProgress" class="flex flex-col gap-0.5">
    <!-- Progress bar -->
    <div
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
