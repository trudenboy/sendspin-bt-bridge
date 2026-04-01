<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { onMounted } from 'vue'

const { t } = useI18n()
const bridge = useBridgeStore()

onMounted(() => {
  bridge.connectSSE()
})
</script>

<template>
  <div>
    <h1 class="mb-6 text-2xl font-bold text-text-primary">
      {{ t('app.dashboard') }}
    </h1>

    <!-- Loading state -->
    <div
      v-if="bridge.loading"
      class="flex items-center justify-center py-20 text-text-secondary"
    >
      {{ t('common.loading') }}
    </div>

    <!-- Devices grid -->
    <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <div
        v-for="device in bridge.devices"
        :key="device.mac"
        class="rounded-[var(--radius-card)] border border-surface-secondary bg-surface-card p-4 shadow-sm transition-shadow hover:shadow-md"
      >
        <div class="mb-2 flex items-center justify-between">
          <h3 class="font-semibold text-text-primary">
            {{ device.player_name }}
          </h3>
          <span
            class="rounded-full px-2 py-0.5 text-xs font-semibold"
            :class="{
              'tone-success': device.audio_streaming,
              'tone-info':
                device.connected && !device.audio_streaming,
              'tone-warning':
                !device.connected && device.enabled,
              'tone-neutral': !device.enabled,
            }"
          >
            {{
              device.audio_streaming
                ? t('device.status.streaming')
                : device.connected
                  ? t('device.status.ready')
                  : device.enabled
                    ? t('device.status.connecting')
                    : t('device.status.offline')
            }}
          </span>
        </div>

        <div class="text-sm text-text-secondary">
          <p>{{ device.mac }}</p>
          <p v-if="device.backend_info">
            {{ t(`device.backend.${device.backend_info.type}`) }}
          </p>
        </div>

        <!-- Volume -->
        <div v-if="device.connected" class="mt-3">
          <input
            type="range"
            min="0"
            max="100"
            :value="device.volume"
            class="w-full accent-primary"
          />
          <span class="text-xs text-text-secondary">
            {{ device.volume }}%
          </span>
        </div>
      </div>

      <!-- Empty state -->
      <div
        v-if="bridge.devices.length === 0"
        class="col-span-full py-16 text-center text-text-secondary"
      >
        {{ t('common.noResults') }}
      </div>
    </div>
  </div>
</template>
