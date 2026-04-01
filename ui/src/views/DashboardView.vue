<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import {
  SbCard,
  SbSpinner,
  SbEmptyState,
  SbFilterBar,
  SbBadge,
} from '@/kit'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import { Bluetooth, Server, Wifi, Speaker } from 'lucide-vue-next'

const { t } = useI18n()
const bridge = useBridgeStore()
const deviceStore = useDeviceStore()

const drawerOpen = ref(false)
const selectedMac = ref<string | null>(null)

onMounted(() => {
  bridge.connectSSE()
})

const statusFilters = computed(() => [
  { key: 'STREAMING', label: t('device.status.streaming'), active: deviceStore.filter.status.includes('STREAMING') },
  { key: 'READY', label: t('device.status.ready'), active: deviceStore.filter.status.includes('READY') },
  { key: 'CONNECTING', label: t('device.status.connecting'), active: deviceStore.filter.status.includes('CONNECTING') },
  { key: 'ERROR', label: t('device.status.error'), active: deviceStore.filter.status.includes('ERROR') },
  { key: 'OFFLINE', label: t('device.status.offline'), active: deviceStore.filter.status.includes('OFFLINE') },
])

const connectedCount = computed(
  () => bridge.devices.filter((d) => d.connected).length,
)

function onToggleFilter(key: string) {
  const arr = deviceStore.filter.status
  const idx = arr.indexOf(key)
  if (idx >= 0) arr.splice(idx, 1)
  else arr.push(key)
}

function openDetail(mac: string) {
  selectedMac.value = mac
  drawerOpen.value = true
}
</script>

<template>
  <div>
    <h1 class="mb-6 text-2xl font-bold text-text-primary">
      {{ t('app.dashboard') }}
    </h1>

    <!-- Loading state -->
    <div v-if="bridge.loading" class="flex flex-col items-center justify-center py-20">
      <SbSpinner size="lg" :label="t('common.loading')" />
    </div>

    <template v-else>
      <!-- Health summary -->
      <SbCard class="mb-6">
        <div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div class="flex items-center gap-3">
            <Server class="h-5 w-5 text-primary" />
            <div>
              <p class="text-xs text-text-secondary">{{ t('dashboard.bridge') }}</p>
              <p class="font-semibold text-text-primary">
                {{ bridge.version || '—' }}
              </p>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <Bluetooth class="h-5 w-5 text-primary" />
            <div>
              <p class="text-xs text-text-secondary">{{ t('dashboard.adapters') }}</p>
              <p class="font-semibold text-text-primary">
                {{ bridge.adapters.length }}
              </p>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <Speaker class="h-5 w-5 text-primary" />
            <div>
              <p class="text-xs text-text-secondary">{{ t('dashboard.devices') }}</p>
              <p class="font-semibold text-text-primary">
                {{ connectedCount }} / {{ bridge.devices.length }}
              </p>
            </div>
          </div>
          <div class="flex items-center gap-3">
            <Wifi class="h-5 w-5 text-primary" />
            <div>
              <p class="text-xs text-text-secondary">{{ t('dashboard.maStatus') }}</p>
              <SbBadge :tone="bridge.maConnected ? 'success' : 'neutral'" size="sm" dot>
                {{ bridge.maConnected ? t('dashboard.maConnected') : t('dashboard.maDisconnected') }}
              </SbBadge>
            </div>
          </div>
        </div>
      </SbCard>

      <!-- Filter bar -->
      <SbFilterBar
        v-model="deviceStore.filter.search"
        :placeholder="t('common.search')"
        :filters="statusFilters"
        class="mb-4"
        @toggle-filter="onToggleFilter"
      />

      <!-- Empty state -->
      <SbEmptyState
        v-if="bridge.devices.length === 0"
        :title="t('dashboard.empty.title')"
        :description="t('dashboard.empty.description')"
      />

      <!-- Device grid -->
      <div
        v-else-if="deviceStore.filteredDevices.length > 0"
        class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      >
        <DeviceCard
          v-for="(device, index) in deviceStore.filteredDevices"
          :key="device.mac"
          :device="device"
          :device-index="index"
          @open-detail="openDetail"
        />
      </div>

      <!-- Filtered empty state -->
      <div
        v-else
        class="py-16 text-center text-text-secondary"
      >
        {{ t('common.noResults') }}
      </div>

      <!-- Detail drawer -->
      <DeviceDetailDrawer
        :device-id="selectedMac"
        :open="drawerOpen"
        @update:open="drawerOpen = $event"
      />
    </template>
  </div>
</template>
