<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import { airChipSummary, signedMs, syncBadge } from '@/utils/deviceChips'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { observeReanchor, retainReanchor } from '@/utils/reanchorHold'
import type { Device } from '@/api/types'

/** "Sync" (with the sync error) or "Re-anchoring" while streaming, plus the re-anchor count. */
const props = defineProps<{ device: Device }>()
const { t, te } = useI18n()

/* The device's re-anchor memory lives while some chip shows it (see reanchorHold). */
let release = retainReanchor(props.device.id)
watch(
  () => props.device.id,
  (id) => {
    release()
    release = retainReanchor(id)
  },
)

/* Re-evaluated on each status update and once more when a hold runs out. */
const expired = ref(0)
const holdUntil = computed(() => {
  void expired.value
  return observeReanchor(props.device)
})
let timer: ReturnType<typeof setTimeout> | null = null
watch(
  holdUntil,
  (until) => {
    if (timer) clearTimeout(timer)
    timer = until == null ? null : setTimeout(() => expired.value++, Math.max(0, until - Date.now()) + 50)
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
  release()
})

const badge = computed(() => syncBadge(props.device, holdUntil.value != null))
const baseTitle = computed(() => {
  const kind = badge.value?.kind
  if (kind === 'reanchoring') return t('device.chips.sync.reanchoringTitle')
  if (kind === 'playout') return t('device.chips.sync.playoutTitle')
  if (kind === 'warming') return t('device.chips.sync.warmingTitle', { s: badge.value?.warmInS ?? 0 })
  return t('device.chips.sync.title')
})
/* One extra line when the air sensor is on and not "off". */
const titleKey = computed(() => {
  const air = airChipSummary(props.device)
  if (!air) return baseTitle.value
  const stateKey = `timing.air.states.${air.state}`
  const state = te(stateKey) ? t(stateKey).toLowerCase() : air.state
  const line = air.hMs == null ? t('timing.air.chipNoH', { state }) : t('timing.air.chip', { state, h: signedMs(air.hMs) })
  return `${baseTitle.value}\n${line}`
})
const labelText = computed(() => {
  const kind = badge.value?.kind
  if (kind === 'reanchoring') return t('device.chips.sync.reanchoring')
  if (kind === 'playout' && technicalDetails.value) return t('device.chips.sync.playout')
  if (kind === 'warming' && technicalDetails.value) return t('device.chips.sync.warming', { s: badge.value?.warmInS ?? 0 })
  return t('device.chips.sync.label')
})
const dash = (n: number | null) => (n == null ? '—' : String(n))
const isPlayout = computed(() => badge.value?.kind === 'playout' || badge.value?.kind === 'warming')
const countTitle = computed(() =>
  badge.value && isPlayout.value
    ? t('device.chips.sync.jumpsTitle', { count: badge.value.count })
    : badge.value
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
      :title="titleKey"
    >
      <SbBadge :tone="badge.tone" size="sm">
        <span
          class="inline-block size-1.5 rounded-full bg-current"
          :class="{ 'animate-pulse': badge.pulse }"
          aria-hidden="true"
        />
        {{ labelText }}
        <span v-if="technicalDetails && badge.errorMs != null" class="tabular-nums opacity-80">{{ t('device.chips.sync.errorMs', { ms: badge.errorMs }) }}</span>
      </SbBadge>
    </span>
    <span v-if="technicalDetails && badge.count > 0" :data-chip="isPlayout ? 'jumps' : 'reanchors'" class="inline-flex" :title="countTitle">
      <SbBadge :tone="badge.countTone" size="sm"><span class="tabular-nums">{{ t('device.chips.sync.count', { count: badge.count }) }}</span></SbBadge>
    </span>
  </template>
</template>
