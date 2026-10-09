<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { SbSlider, SbToggle, SbCard } from '@/kit'

const { t } = useI18n()
const configStore = useConfigStore()

const pulseLatency = computed({
  get: () => configStore.config?.PULSE_LATENCY_MSEC ?? 800,
  set: (v: number) => configStore.updateField('PULSE_LATENCY_MSEC', v),
})

const preferSbc = computed({
  get: () => configStore.config?.PREFER_SBC_CODEC ?? false,
  set: (v: boolean) => configStore.updateField('PREFER_SBC_CODEC', v),
})

const staticDelay = computed({
  get: () => (configStore.config as Record<string, unknown>)?.STATIC_DELAY_MS as number ?? -300,
  set: (v: number) => configStore.updateField('STATIC_DELAY_MS', v),
})

function formatMs(v: number): string {
  return `${v} ${t('config.pulseLatencyUnit')}`
}

/* Latency presets */
const latencyPresets = [
  { key: 'config.latencyLow', value: 200 },
  { key: 'config.latencyStandard', value: 500 },
  { key: 'config.latencyHigh', value: 800 },
] as const

const activePreset = computed(() => {
  const v = pulseLatency.value
  const match = latencyPresets.find((p) => p.value === v)
  return match ? match.value : 'custom'
})

function applyPreset(value: number) {
  pulseLatency.value = value
}
</script>

<template>
  <div class="space-y-6 py-6">
    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.audio') }}</h3>
      </template>
      <div class="space-y-6">
        <div>
          <SbSlider
            v-model="pulseLatency"
            :label="t('config.pulseLatency')"
            :min="0"
            :max="2000"
            :step="100"
            show-value
            :format-value="formatMs"
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.pulseLatencyHint') }}
          </p>
          <div class="mt-3">
            <p class="mb-1.5 text-xs font-medium text-text-secondary">{{ t('config.latencyPresets') }}</p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="preset in latencyPresets"
                :key="preset.value"
                class="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
                :class="activePreset === preset.value
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-gray-300 text-text-secondary hover:border-primary/50 hover:text-text-primary dark:border-gray-600'"
                @click="applyPreset(preset.value)"
              >
                {{ t(preset.key) }}
              </button>
              <span
                class="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
                :class="activePreset === 'custom'
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-gray-300 text-text-secondary dark:border-gray-600'"
              >
                {{ t('config.latencyCustom') }}
              </span>
            </div>
          </div>
        </div>

        <div>
          <SbToggle
            v-model="preferSbc"
            :label="t('config.preferSbc')"
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.preferSbcHint') }}
          </p>
        </div>

        <div>
          <SbSlider
            v-model="staticDelay"
            :label="t('config.staticDelay')"
            :min="-1000"
            :max="0"
            :step="50"
            show-value
            :format-value="formatMs"
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.staticDelayHint') }}
          </p>
        </div>
      </div>
    </SbCard>
  </div>
</template>
