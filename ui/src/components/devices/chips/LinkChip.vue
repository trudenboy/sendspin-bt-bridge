<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { Bluetooth, Speaker } from 'lucide-vue-next'
import { SbBadge } from '@/kit'
import { useAudioStore } from '@/stores/audio'
import { linkChip, type LinkChip as LinkChipData } from '@/utils/deviceChips'
import type { Device } from '@/api/types'

/**
 * A Bluetooth speaker's adapter, coloured by its link and opening the
 * adapter in Configuration; a local output's audio output (no link).
 */
const props = defineProps<{ device?: Device; chip?: LinkChipData | null }>()
const { t } = useI18n()
const router = useRouter()
const audio = useAudioStore()

/* A ready-made chip (a disabled player has no Device), else the device's own. */
const chip = computed(() => (props.chip !== undefined ? props.chip : props.device ? linkChip(props.device, audio.sinks) : null))

function openAdapter() {
  if (chip.value?.kind !== 'adapter' || !chip.value.hci) return
  void router?.push({ name: 'config', query: { adapter: chip.value.hci }, hash: '#settings-bluetooth' })
}
</script>

<template>
  <template v-if="chip">
    <button
      v-if="chip.kind === 'adapter'"
      type="button"
      data-chip="adapter"
      class="inline-flex max-w-full rounded-(--radius-badge)"
      :title="t('device.chips.adapter.title', { name: chip.label, hci: chip.hci, state: t(`device.chips.adapter.state.${chip.state}`) })"
      :aria-label="t('device.chips.adapter.open', { name: chip.label })"
      @click.stop="openAdapter"
    >
      <SbBadge :tone="chip.tone" size="sm" class="max-w-full hover:brightness-95">
        <Bluetooth class="size-3 shrink-0" :class="{ 'animate-pulse': chip.pulse }" aria-hidden="true" />
        <span class="min-w-0 truncate">{{ chip.label }}</span>
      </SbBadge>
    </button>
    <span
      v-else
      data-chip="output"
      class="inline-flex max-w-full"
      :title="t('device.chips.output.title', { name: chip.sink })"
    >
      <SbBadge tone="neutral" size="sm" class="max-w-full">
        <Speaker class="size-3 shrink-0" aria-hidden="true" />
        <span class="min-w-0 truncate">{{ chip.label }}</span>
      </SbBadge>
    </span>
  </template>
</template>
