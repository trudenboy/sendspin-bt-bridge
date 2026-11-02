<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Radio } from 'lucide-vue-next'
import { useBridgeStore } from '@/stores/bridge'
import { SbBadge } from '@/kit'

/**
 * A one-line answer to "why is my phone silent?" on the page the operator
 * opens first.  The controls stay on Hardware and in Settings; this card only
 * reports and links, so it never competes with the speaker grid for attention.
 */
const { t } = useI18n()
const bridge = useBridgeStore()

const source = computed(() => bridge.bridge?.sendspin_source ?? null)
const settingsHref = '/config#settings-musicAssistant'

const stateText = computed(() => {
  const state = source.value?.state ?? 'off'
  if (state === 'streaming') return t('home.source.streaming')
  if (state === 'pairing') return t('home.source.pairing')
  if (state === 'error') return t('home.source.error')
  if (!source.value?.enabled) return t('home.source.off')
  return t('home.source.waiting')
})
const maText = computed(() => {
  const source_ = source.value
  if (source_?.ma_connected) return t('home.source.maConnected')
  if (source_?.ma_paired) return t('home.source.maPaired')
  return t('home.source.maUnpaired')
})
const tone = computed(() => {
  if (source.value?.state === 'error') return 'error'
  if (source.value?.state === 'streaming') return 'success'
  return 'neutral'
})
</script>

<template>
  <section
    v-if="source"
    data-home-source
    class="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-(--radius-card) border border-border bg-surface-card px-4 py-3"
  >
    <Radio class="size-4 shrink-0 text-text-secondary" aria-hidden="true" />
    <div class="min-w-0 flex-1">
      <p class="text-sm font-medium text-text-primary">{{ t('home.source.title') }}</p>
      <p class="mt-0.5 text-xs text-text-secondary" data-home-source-status>
        {{ stateText }} · {{ maText }}
      </p>
    </div>
    <SbBadge :tone="tone" size="sm">{{ stateText }}</SbBadge>
    <RouterLink
      v-if="!source.ma_paired"
      :to="settingsHref"
      data-home-source-pair
      class="text-sm text-primary-text underline-offset-2 hover:underline"
    >
      {{ t('home.source.pairAction') }}
    </RouterLink>
    <RouterLink
      to="/hardware"
      data-home-source-hardware
      class="text-sm text-primary-text underline-offset-2 hover:underline"
    >
      {{ t('home.source.openHardware') }}
    </RouterLink>
  </section>
</template>
