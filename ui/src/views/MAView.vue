<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { SbCard, SbStatusDot } from '@/kit'
import MaLoginFlow from '@/components/ma/MaLoginFlow.vue'
import MaGroupList from '@/components/ma/MaGroupList.vue'

const { t } = useI18n()
const ma = useMaStore()
const bridge = useBridgeStore()
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

    <!-- Login or groups -->
    <MaLoginFlow v-if="!ma.connected && !bridge.maConnected" />
    <MaGroupList v-else />
  </div>
</template>
