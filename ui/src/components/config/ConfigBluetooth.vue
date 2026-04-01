<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'
import { SbSlider, SbCard, SbBadge } from '@/kit'

const { t } = useI18n()
const configStore = useConfigStore()
const bridgeStore = useBridgeStore()

const btCheckInterval = computed({
  get: () => configStore.config?.BT_CHECK_INTERVAL ?? 15,
  set: (v: number) => configStore.updateField('BT_CHECK_INTERVAL', v),
})

const btMaxReconnect = computed({
  get: () => configStore.config?.BT_MAX_RECONNECT_FAILS ?? 10,
  set: (v: number) => configStore.updateField('BT_MAX_RECONNECT_FAILS', v),
})

function formatSec(v: number): string {
  return `${v} ${t('config.btCheckIntervalUnit')}`
}
</script>

<template>
  <div class="space-y-6 py-6">
    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.bluetooth') }}</h3>
      </template>
      <div class="space-y-6">
        <div>
          <SbSlider
            v-model="btCheckInterval"
            :label="t('config.btCheckInterval')"
            :min="5"
            :max="60"
            :step="5"
            show-value
            :format-value="formatSec"
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.btCheckIntervalHint') }}
          </p>
        </div>

        <div>
          <SbSlider
            v-model="btMaxReconnect"
            :label="t('config.btMaxReconnect')"
            :min="1"
            :max="50"
            :step="1"
            show-value
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.btMaxReconnectHint') }}
          </p>
        </div>

        <p class="text-sm text-text-secondary italic">
          {{ t('config.managementMode') }}
        </p>
      </div>
    </SbCard>

    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.adapterList') }}</h3>
      </template>
      <div v-if="bridgeStore.adapters.length === 0" class="py-4 text-center text-sm text-text-secondary">
        {{ t('config.noAdapters') }}
      </div>
      <div v-else class="divide-y divide-gray-200 dark:divide-gray-700">
        <div
          v-for="adapter in bridgeStore.adapters"
          :key="adapter.hci_device"
          class="flex items-center justify-between px-1 py-3"
        >
          <div>
            <p class="text-sm font-medium text-text-primary">
              {{ adapter.name || adapter.hci_device }}
            </p>
            <p class="text-xs text-text-secondary">
              {{ adapter.hci_device }} · {{ adapter.mac }}
            </p>
          </div>
          <SbBadge :tone="adapter.powered ? 'success' : 'neutral'">
            {{ adapter.powered ? t('config.adapterPowered') : t('config.adapterOff') }}
          </SbBadge>
        </div>
      </div>
    </SbCard>
  </div>
</template>
