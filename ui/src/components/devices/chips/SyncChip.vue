<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import { syncBadge } from '@/utils/deviceChips'
import type { Device } from '@/api/types'

/** "Sync" (with the sync error) or "Re-anchoring" while streaming, plus the re-anchor count. */
const props = defineProps<{ device: Device }>()
const { t } = useI18n()

const badge = computed(() => syncBadge(props.device))
const dash = (n: number | null) => (n == null ? '—' : String(n))
const countTitle = computed(() =>
  badge.value
    ? t('device.chips.sync.countTitle', {
        count: badge.value.count,
        m5: dash(badge.value.count5m),
        m30: dash(badge.value.count30m),
      })
    : '',
)
</script>

<template>
  <template v-if="badge">
    <span
      data-chip="sync"
      class="inline-flex"
      :title="badge.kind === 'reanchoring' ? t('device.chips.sync.reanchoringTitle') : t('device.chips.sync.title')"
    >
      <SbBadge :tone="badge.tone" size="sm">
        <span
          class="inline-block size-1.5 rounded-full bg-current"
          :class="{ 'animate-pulse': badge.pulse }"
          aria-hidden="true"
        />
        {{ badge.kind === 'reanchoring' ? t('device.chips.sync.reanchoring') : t('device.chips.sync.label') }}
        <span v-if="badge.errorMs != null" class="tabular-nums opacity-80">{{ t('device.chips.sync.errorMs', { ms: badge.errorMs }) }}</span>
      </SbBadge>
    </span>
    <span v-if="badge.count > 0" data-chip="reanchors" class="inline-flex" :title="countTitle">
      <SbBadge :tone="badge.countTone" size="sm"><span class="tabular-nums">{{ t('device.chips.sync.count', { count: badge.count }) }}</span></SbBadge>
    </span>
  </template>
</template>
