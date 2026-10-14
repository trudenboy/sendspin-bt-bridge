<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import { syncBadge } from '@/utils/deviceChips'
import { observeReanchor, retainReanchor } from '@/utils/reanchorHold'
import type { Device } from '@/api/types'

/** "Sync" (with the sync error) or "Re-anchoring" while streaming, plus the re-anchor count. */
const props = defineProps<{ device: Device }>()
const { t } = useI18n()

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
