<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { api, unwrap } from '@/api/client'
import SettingsRow from './SettingsRow.vue'

interface Assistant {
  summary?: string
  recommended_pulse_latency_msec?: number
  recommended_summary?: string
  presets?: { key: string; label: string; value: number; summary: string }[]
}

const { t } = useI18n()
const configStore = useConfigStore()
const data = ref<Assistant | null>(null)

onMounted(async () => {
  try {
    data.value = (await unwrap(api().GET('/api/v1/latency/recommendations'))) as Assistant
  } catch {
    data.value = null
  }
})

// Edits the document like any field, so the floating Save applies it.
function choose(value: number) {
  configStore.updateField('PULSE_LATENCY_MSEC', value)
}
</script>

<template>
  <SettingsRow
    v-if="data?.presets?.length"
    :label="t('settings.parts.latency.label')"
    :help="[data.summary, data.recommended_summary].filter(Boolean).join(' ')"
  >
    <div class="flex flex-wrap gap-1.5" role="group" :aria-label="t('settings.parts.latency.label')">
      <button
        v-for="p in data.presets"
        :key="p.key"
        type="button"
        :title="p.summary"
        class="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
        :class="
          configStore.config?.PULSE_LATENCY_MSEC === p.value
            ? 'border-primary bg-primary/12 text-primary-text'
            : 'border-border-strong text-text-secondary hover:text-text-primary'
        "
        :aria-pressed="configStore.config?.PULSE_LATENCY_MSEC === p.value"
        @click="choose(p.value)"
      >
        {{ p.label }}<span v-if="p.value === data.recommended_pulse_latency_msec"> · {{ t('settings.parts.latency.recommended') }}</span>
      </button>
    </div>
  </SettingsRow>
</template>
