<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { fleetSummary } from '@/utils/fleetSummary'
import { useTheme } from '@/composables/useTheme'
import StatusState from '@/components/StatusState.vue'

/**
 * The bridge at a glance, under the header (stable's runtime line): where it
 * runs, host, address and uptime, then speakers, Music Assistant and playback.
 */
const { t, te } = useI18n()
const bridge = useBridgeStore()

const { embedded } = useTheme()
const info = computed(() => bridge.bridge)
const fleet = computed(() => fleetSummary(bridge.devices))

const runtimeLabel = computed(() => {
  const runtime = (info.value?.runtime || '').replace(/-/g, '_')
  if (!runtime || runtime === 'unknown') return ''
  const key = `header.runtime.${runtime}`
  return te(key) ? t(key) : runtime.toUpperCase()
})
const mode = computed(() => {
  const m = info.value?.runtime_mode || 'production'
  return m === 'production' ? '' : m
})

const speakersTone = computed(() => {
  const { ok, total } = fleet.value
  if (ok === total) return 'tone-success'
  return ok > 0 ? 'tone-warning' : 'tone-error'
})

const pill = 'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium'
</script>

<template>
  <div
    v-if="info && !embedded"
    class="border-b border-border bg-surface-card px-4 py-1.5 text-xs text-text-secondary sm:px-6"
    :aria-label="t('header.runtimeRow')"
    role="group"
  >
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span v-if="runtimeLabel" data-runtime="runtime" :class="[pill, 'tone-neutral uppercase tracking-wide']">{{ runtimeLabel }}</span>
      <span v-if="mode" data-runtime="mode" :class="[pill, 'tone-info']">{{ mode }}</span>
      <span v-if="info.hostname" data-runtime="host" class="truncate" :title="t('header.hostname')">{{ info.hostname }}</span>
      <span v-if="info.ip_address" data-runtime="ip" class="tabular-nums" :title="t('header.ipAddress')">{{ info.ip_address }}</span>
      <span v-if="info.uptime" data-runtime="uptime" class="tabular-nums">{{ t('header.upFor', { uptime: info.uptime }) }}</span>

      <span class="ml-auto flex flex-wrap items-center gap-1.5">
        <span v-if="fleet.total > 0" data-runtime="speakers" :class="[pill, speakersTone]">
          {{ t('header.speakersConnected', { n: fleet.connected, total: fleet.total }) }}
        </span>
        <StatusState data-runtime="ma" kind="ma_account" :status="bridge.maAccount" variant="full" />
        <span v-if="fleet.playing > 0" data-runtime="playing" :class="[pill, 'tone-info']">
          {{ t('home.playing', { n: fleet.playing }) }}
        </span>
      </span>
    </div>
  </div>
</template>
