<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'
import { SbInput, SbToggle, SbCard, SbBadge } from '@/kit'

const { t } = useI18n()
const configStore = useConfigStore()
const bridgeStore = useBridgeStore()

const maServer = computed({
  get: () => configStore.config?.SENDSPIN_SERVER ?? 'auto',
  set: (v: string) => configStore.updateField('SENDSPIN_SERVER', v),
})

const maPort = computed({
  get: () => String(configStore.config?.SENDSPIN_PORT ?? 9000),
  set: (v: string) => configStore.updateField('SENDSPIN_PORT', Number(v)),
})

const autoDiscovery = computed({
  get: () => (configStore.config?.SENDSPIN_SERVER ?? 'auto') === 'auto',
  set: (v: boolean) => {
    if (v) {
      configStore.updateField('SENDSPIN_SERVER', 'auto')
    } else {
      configStore.updateField('SENDSPIN_SERVER', '')
    }
  },
})
</script>

<template>
  <div class="space-y-6 py-6">
    <SbCard>
      <template #header>
        <div class="flex items-center gap-3">
          <h3 class="text-lg font-semibold text-text-primary">{{ t('config.ma') }}</h3>
          <SbBadge :tone="bridgeStore.maConnected ? 'success' : 'neutral'">
            {{ bridgeStore.maConnected ? t('config.maConnected') : t('config.maDisconnected') }}
          </SbBadge>
        </div>
      </template>
      <div class="space-y-5">
        <div>
          <SbToggle
            v-model="autoDiscovery"
            :label="t('config.autoDiscovery')"
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.autoDiscoveryHint') }}
          </p>
        </div>

        <SbInput
          v-model="maServer"
          :label="t('config.maServer')"
          :hint="t('config.maServerHint')"
          :disabled="autoDiscovery"
          placeholder="auto"
        />

        <SbInput
          v-model="maPort"
          :label="t('config.maPort')"
          type="number"
          placeholder="9000"
        />

        <div>
          <label class="mb-1 block text-sm font-medium text-text-primary">
            {{ t('config.connectionStatus') }}
          </label>
          <div class="flex items-center gap-2">
            <span
              class="inline-block h-2.5 w-2.5 rounded-full"
              :class="bridgeStore.maConnected ? 'bg-green-500' : 'bg-gray-400'"
            />
            <span class="text-sm text-text-secondary">
              {{ bridgeStore.maConnected ? t('config.maConnected') : t('config.maDisconnected') }}
            </span>
            <span v-if="bridgeStore.snapshot?.ma_url" class="text-xs text-text-secondary">
              ({{ bridgeStore.snapshot.ma_url }})
            </span>
          </div>
        </div>
      </div>
    </SbCard>
  </div>
</template>
