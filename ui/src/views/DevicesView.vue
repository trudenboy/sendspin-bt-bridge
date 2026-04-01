<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import { SbFilterBar, SbButton, SbSpinner, SbEmptyState } from '@/kit'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import BtScanModal from '@/components/bluetooth/BtScanModal.vue'
import { Plus, Bluetooth } from 'lucide-vue-next'

const { t } = useI18n()
const bridge = useBridgeStore()
const deviceStore = useDeviceStore()

const drawerOpen = ref(false)
const selectedMac = ref<string | null>(null)
const scanModalOpen = ref(false)

onMounted(() => {
  if (!bridge.snapshot) bridge.connectSSE()
})

const statusFilters = computed(() => [
  { key: 'STREAMING', label: t('device.status.streaming'), active: deviceStore.filter.status.includes('STREAMING') },
  { key: 'READY', label: t('device.status.ready'), active: deviceStore.filter.status.includes('READY') },
  { key: 'CONNECTING', label: t('device.status.connecting'), active: deviceStore.filter.status.includes('CONNECTING') },
  { key: 'ERROR', label: t('device.status.error'), active: deviceStore.filter.status.includes('ERROR') },
  { key: 'OFFLINE', label: t('device.status.offline'), active: deviceStore.filter.status.includes('OFFLINE') },
])

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
    <div class="mb-6 flex items-center justify-between">
      <h1 class="text-2xl font-bold text-text-primary">
        {{ t('app.devices') }}
      </h1>
      <div class="flex gap-2">
        <SbButton variant="secondary" size="sm" @click="scanModalOpen = true">
          <template #icon-left>
            <Bluetooth class="h-4 w-4" />
          </template>
          {{ t('bluetooth.scan.title') }}
        </SbButton>
        <SbButton size="sm" :disabled="true">
          <template #icon-left>
            <Plus class="h-4 w-4" />
          </template>
          {{ t('devices.addDevice') }}
        </SbButton>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="bridge.loading" class="flex flex-col items-center justify-center py-20">
      <SbSpinner size="lg" :label="t('common.loading')" />
    </div>

    <template v-else>
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
        :title="t('devices.empty.title')"
        :description="t('devices.empty.description')"
      >
        <template #action>
          <SbButton @click="scanModalOpen = true">
            <template #icon-left>
              <Bluetooth class="h-4 w-4" />
            </template>
            {{ t('bluetooth.scan.title') }}
          </SbButton>
        </template>
      </SbEmptyState>

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

      <!-- No filter results -->
      <div v-else class="py-16 text-center text-text-secondary">
        {{ t('common.noResults') }}
      </div>
    </template>

    <!-- Detail drawer -->
    <DeviceDetailDrawer
      :device-id="selectedMac"
      :open="drawerOpen"
      @update:open="drawerOpen = $event"
    />

    <!-- BT Scan modal -->
    <BtScanModal
      :open="scanModalOpen"
      @update:open="scanModalOpen = $event"
    />
  </div>
</template>
