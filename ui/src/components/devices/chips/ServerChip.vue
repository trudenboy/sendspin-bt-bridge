<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Server, TriangleAlert } from 'lucide-vue-next'
import { SbBadge } from '@/kit'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { serverChip } from '@/utils/deviceChips'
import type { Device } from '@/api/types'

/** The Music Assistant the player is connected to; a warning when it is not the bridge's own. */
const props = defineProps<{ device: Device }>()
const { t } = useI18n()

const chip = computed(() => serverChip(props.device))
const where = computed(() => {
  const c = chip.value
  if (!c) return ''
  return c.name && c.host ? `${c.host} (${c.name})` : c.label
})
const title = computed(() =>
  chip.value?.foreign ? t('device.chips.server.foreign', { where: where.value }) : t('device.chips.server.title', { where: where.value }),
)
</script>

<template>
  <span v-if="chip && (technicalDetails || chip.foreign)" data-chip="server" class="inline-flex max-w-full" :title="title">
    <SbBadge :tone="chip.tone" size="sm" class="max-w-full">
      <TriangleAlert v-if="chip.foreign" class="size-3 shrink-0" aria-hidden="true" />
      <Server v-else class="size-3 shrink-0" aria-hidden="true" />
      <span class="min-w-0 truncate">{{ chip.label }}</span>
      <span class="sr-only">{{ title }}</span>
    </SbBadge>
  </span>
</template>
