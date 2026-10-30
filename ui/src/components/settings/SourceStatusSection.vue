<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getStatus } from '@/api/status'
import { SbBadge } from '@/kit'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const source = ref<{ enabled: boolean; port: number | null; phone: string | null; streaming: boolean; error: string | null } | null>(null)

onMounted(async () => {
  try {
    const status = await getStatus()
    source.value = status.bridge.sendspin_source ?? null
  } catch {
    source.value = null
  }
})

const state = computed(() => {
  // Configured but not running yet (settings saved, bridge not restarted): still off.
  if (!source.value || !source.value.enabled) return 'off'
  if (source.value.error) return 'error'
  if (source.value.streaming) return 'streaming'
  return 'waiting'
})
const tone = computed(() => (state.value === 'streaming' ? 'success' : state.value === 'off' ? 'neutral' : 'warning'))
const detail = computed(() => {
  const s = source.value
  if (!s) return t('settings.parts.sourceStatus.off')
  if (s.error) return t('settings.parts.sourceStatus.error')
  if (s.streaming && s.phone) return t('settings.parts.sourceStatus.streaming', { phone: s.phone, port: s.port })
  return t('settings.parts.sourceStatus.waiting', { port: s.port })
})
</script>

<template>
  <SettingsRow :label="t('settings.parts.sourceStatus.label')" :help="detail">
    <SbBadge :tone="tone" size="sm">{{ t(`settings.parts.sourceStatus.${state}`) }}</SbBadge>
  </SettingsRow>
</template>
