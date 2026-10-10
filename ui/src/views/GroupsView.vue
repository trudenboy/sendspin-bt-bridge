<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { useMaAutoConnect } from '@/composables/useMaAutoConnect'
import { SbStatusDot } from '@/kit'
import MaLoginFlow from '@/components/ma/MaLoginFlow.vue'
import MaGroupList from '@/components/ma/MaGroupList.vue'
import { Loader2 } from 'lucide-vue-next'

const { t } = useI18n()
const ma = useMaStore()
const bridge = useBridgeStore()
const { autoConnecting, autoConnectFailed } = useMaAutoConnect()
</script>

<template>
  <div>
    <div class="mb-6 flex flex-wrap items-baseline justify-between gap-2">
      <h1 class="text-2xl font-semibold tracking-tight text-text-primary">{{ t('nav.groups') }}</h1>
      <p v-if="bridge.maConnected" class="inline-flex items-center gap-1.5 text-sm text-text-secondary">
        <SbStatusDot status="online" size="sm" :label="t('ma.connection.connected')" />
        {{ t('groups.via', { url: bridge.bridge?.ma_web_url ?? 'Music Assistant' }) }}
      </p>
    </div>

    <div v-if="autoConnecting" class="mb-6 flex items-center gap-3 rounded-(--radius-card) border border-border bg-surface-card p-4">
      <Loader2 class="h-5 w-5 animate-spin text-text-secondary" aria-hidden="true" />
      <span class="text-sm text-text-secondary">{{ t('ma.autoConnecting') }}</span>
    </div>

    <div
      v-if="autoConnectFailed && !ma.connected && !bridge.maConnected"
      class="mb-4 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-2 text-sm text-text-primary"
    >
      {{ t('ma.silentAuthFailed') }}
    </div>

    <template v-if="!autoConnecting && !ma.connected && !bridge.maConnected">
      <p class="mb-4 text-sm text-text-secondary">{{ t('groups.connectFirst') }}</p>
      <MaLoginFlow />
    </template>
    <MaGroupList v-if="!autoConnecting && (ma.connected || bridge.maConnected)" />
  </div>
</template>
