<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { useMaAutoConnect } from '@/composables/useMaAutoConnect'
import { SbCard, SbStatusDot } from '@/kit'
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
    <h1 class="mb-6 text-2xl font-bold text-text-primary">
      {{ t('app.ma') }}
    </h1>

    <!-- Connection status -->
    <SbCard class="mb-6">
      <template #header>
        <span>{{ t('ma.connection.title') }}</span>
      </template>
      <div class="flex items-center gap-3">
        <SbStatusDot :status="bridge.maConnected ? 'ready' : 'offline'" />
        <span class="text-sm text-text-primary">
          {{ bridge.maConnected ? t('ma.connection.connected') : t('ma.connection.disconnected') }}
        </span>
        <span v-if="bridge.snapshot?.ma_url" class="text-sm text-text-secondary">
          — {{ bridge.snapshot.ma_url }}
        </span>
      </div>
    </SbCard>

    <!-- Auto-connecting spinner -->
    <div
      v-if="autoConnecting"
      class="mb-6 flex items-center gap-3 rounded-lg border border-border-secondary bg-bg-secondary p-4"
    >
      <Loader2 class="h-5 w-5 animate-spin text-text-secondary" aria-hidden="true" />
      <span class="text-sm text-text-secondary">{{ t('ma.autoConnecting') }}</span>
    </div>

    <!-- Auto-connect failed notice -->
    <div
      v-if="autoConnectFailed && !ma.connected && !bridge.maConnected"
      class="mb-4 rounded-lg border border-yellow-300 bg-yellow-50 px-4 py-2 text-sm text-yellow-800 dark:border-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-200"
    >
      {{ t('ma.silentAuthFailed') }}
    </div>

    <!-- Login or groups -->
    <MaLoginFlow v-if="!autoConnecting && !ma.connected && !bridge.maConnected" />
    <MaGroupList v-if="!autoConnecting && (ma.connected || bridge.maConnected)" />
  </div>
</template>
