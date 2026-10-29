<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { signalInfo } from '@/utils/deviceChips'
import { useNow } from '@/composables/useNow'
import type { Device } from '@/api/types'

/** Four signal bars and the link's Δ dB offset, coloured by tier (stable's scheme). */
const props = defineProps<{ device: Device }>()
const { t } = useI18n()

const now = useNow()
const info = computed(() => signalInfo(props.device, now.value))
const color = computed(() => {
  const tone = info.value?.tone
  if (info.value?.stale || tone === 'neutral') return 'text-text-disabled'
  return tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning-text' : 'text-error'
})
const HEIGHTS = ['h-1', 'h-1.5', 'h-2', 'h-2.5']
</script>

<template>
  <span
    v-if="info"
    data-chip="signal"
    class="inline-flex items-center gap-1 text-xs"
    :class="color"
    :title="t(info.stale ? 'device.chips.signal.staleTitle' : 'device.chips.signal.title', { value: info.value })"
  >
    <span class="inline-flex h-2.5 items-end gap-px" aria-hidden="true">
      <span
        v-for="(h, i) in HEIGHTS"
        :key="i"
        data-signal-bar
        class="w-0.5 rounded-sm"
        :class="[h, i < info.bars ? 'is-on bg-current' : 'bg-current opacity-25']"
      />
    </span>
    <span class="tabular-nums">{{ t('device.chips.signal.value', { value: info.value }) }}</span>
    <span class="sr-only">{{ t(`device.chips.signal.tier.${info.stale ? 'stale' : info.tier}`) }}</span>
  </span>
</template>
