<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import { useDeviceSelection } from '@/composables/useDeviceSelection'
import { SbFilterBar, SbButton, SbSpinner, SbEmptyState, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceListRow from '@/components/devices/DeviceListRow.vue'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import GroupActionBar from '@/components/devices/GroupActionBar.vue'
import BtScanModal from '@/components/bluetooth/BtScanModal.vue'
import { Plus, Bluetooth, LayoutGrid, List, ChevronDown } from 'lucide-vue-next'

type ViewMode = 'grid' | 'list'
const STORAGE_KEY = 'sb-view-mode'

const { t } = useI18n()
const bridge = useBridgeStore()
const deviceStore = useDeviceStore()

const drawerOpen = ref(false)
const selectedMac = ref<string | null>(null)
const scanModalOpen = ref(false)
const viewMode = ref<ViewMode>(
  (localStorage.getItem(STORAGE_KEY) as ViewMode) || 'grid',
)

const selection = useDeviceSelection(
  computed(() => deviceStore.filteredDevices),
)

const showGroupBar = computed(() => bridge.devices.length >= 2)
const selectable = computed(() => bridge.devices.length >= 2)

onMounted(() => {
  if (!bridge.snapshot) bridge.connectSSE()
})

function setViewMode(mode: ViewMode) {
  viewMode.value = mode
  localStorage.setItem(STORAGE_KEY, mode)
}

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

/* Adapter filter options */
const uniqueAdapters = computed(() => {
  const set = new Set<string>()
  for (const d of bridge.devices) {
    if (d.adapter) set.add(d.adapter)
  }
  return [...set].sort()
})

const adapterLabel = computed(() =>
  deviceStore.filter.adapter || t('devices.allAdapters'),
)

function setAdapterFilter(adapter: string) {
  deviceStore.filter.adapter = adapter
}

/* Group filter options */
const groupLabel = computed(() => {
  if (!deviceStore.filter.group) return t('devices.allGroups')
  const g = bridge.groups.find((g) => g.group_id === deviceStore.filter.group)
  return g?.group_name ?? deviceStore.filter.group
})

function setGroupFilter(groupId: string) {
  deviceStore.filter.group = groupId
  if (groupId) {
    const group = bridge.groups.find((g) => g.group_id === groupId)
    if (group) {
      selection.selectGroup(group.members.map((m) => m.player_name))
    }
  }
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
      <div class="flex items-center gap-2">
        <!-- View mode toggle -->
        <div class="flex rounded-lg border border-border">
          <button
            type="button"
            class="rounded-l-lg p-1.5 transition-colors"
            :class="viewMode === 'grid' ? 'bg-primary text-white' : 'text-text-secondary hover:bg-surface-secondary'"
            :aria-label="t('devices.viewGrid')"
            @click="setViewMode('grid')"
          >
            <LayoutGrid class="h-4 w-4" />
          </button>
          <button
            type="button"
            class="rounded-r-lg p-1.5 transition-colors"
            :class="viewMode === 'list' ? 'bg-primary text-white' : 'text-text-secondary hover:bg-surface-secondary'"
            :aria-label="t('devices.viewList')"
            @click="setViewMode('list')"
          >
            <List class="h-4 w-4" />
          </button>
        </div>

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
      <div class="mb-4 flex flex-wrap items-center gap-2">
        <SbFilterBar
          v-model="deviceStore.filter.search"
          :placeholder="t('common.search')"
          :filters="statusFilters"
          class="flex-1"
          @toggle-filter="onToggleFilter"
        />

        <!-- Adapter filter dropdown -->
        <SbDropdown v-if="uniqueAdapters.length > 1" align="right">
          <template #trigger>
            <button
              type="button"
              aria-haspopup="true"
              class="inline-flex cursor-pointer items-center gap-1 rounded-[--radius-button] border border-gray-300 bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-secondary dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            >
              {{ adapterLabel }}
              <ChevronDown class="h-4 w-4" />
            </button>
          </template>
          <SbDropdownItem @click="setAdapterFilter('')">
            {{ t('devices.allAdapters') }}
          </SbDropdownItem>
          <SbDropdownItem
            v-for="adapter in uniqueAdapters"
            :key="adapter"
            @click="setAdapterFilter(adapter)"
          >
            {{ adapter }}
          </SbDropdownItem>
        </SbDropdown>

        <!-- Group filter dropdown -->
        <SbDropdown v-if="bridge.groups.length > 0" align="right">
          <template #trigger>
            <button
              type="button"
              aria-haspopup="true"
              class="inline-flex cursor-pointer items-center gap-1 rounded-[--radius-button] border border-gray-300 bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-secondary dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            >
              {{ groupLabel }}
              <ChevronDown class="h-4 w-4" />
            </button>
          </template>
          <SbDropdownItem @click="setGroupFilter('')">
            {{ t('devices.allGroups') }}
          </SbDropdownItem>
          <SbDropdownItem
            v-for="group in bridge.groups"
            :key="group.group_id"
            @click="setGroupFilter(group.group_id)"
          >
            {{ group.group_name }}
          </SbDropdownItem>
        </SbDropdown>
      </div>

      <!-- Group action bar -->
      <GroupActionBar
        v-if="showGroupBar"
        :devices="deviceStore.filteredDevices"
        :total="deviceStore.filteredDevices.length"
        :selected-count="selection.selectedCount.value"
        :all-selected="selection.allSelected.value"
        :some-selected="selection.someSelected.value"
        :selected-devices="selection.selectedDevices.value"
        :selected-names="selection.selectedNames.value"
        @toggle-all="selection.toggleAll()"
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
        v-else-if="viewMode === 'grid' && deviceStore.filteredDevices.length > 0"
        class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      >
        <DeviceCard
          v-for="(device, index) in deviceStore.filteredDevices"
          :key="device.mac"
          :device="device"
          :device-index="index"
          :selectable="selectable"
          :selected="selection.selected.value.has(device.player_name)"
          @open-detail="openDetail"
          @toggle-select="selection.toggle($event)"
        />
      </div>

      <!-- Device list -->
      <div
        v-else-if="viewMode === 'list' && deviceStore.filteredDevices.length > 0"
        class="overflow-x-auto rounded-lg border border-border"
      >
        <table class="w-full text-left text-sm">
          <thead>
            <tr class="border-b border-border bg-surface-secondary text-xs uppercase tracking-wider text-text-secondary">
              <th v-if="selectable" class="w-10 py-2 pl-3 pr-1 font-medium" />
              <th class="py-2 pl-3 pr-2 font-medium">{{ t('devices.list.name') }}</th>
              <th class="px-2 py-2 font-medium">{{ t('devices.list.status') }}</th>
              <th class="px-2 py-2 font-medium">{{ t('devices.list.volume') }}</th>
              <th class="px-2 py-2 font-medium">{{ t('devices.list.transport') }}</th>
              <th class="hidden px-2 py-2 font-medium md:table-cell">{{ t('devices.list.adapter') }}</th>
              <th class="py-2 pl-2 pr-3 text-right font-medium">{{ t('devices.list.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <DeviceListRow
              v-for="(device, index) in deviceStore.filteredDevices"
              :key="device.mac"
              :device="device"
              :device-index="index"
              :selectable="selectable"
              :selected="selection.selected.value.has(device.player_name)"
              @open-detail="openDetail"
              @toggle-select="selection.toggle($event)"
            />
          </tbody>
        </table>
      </div>

      <!-- No filter results -->
      <div v-else-if="deviceStore.filteredDevices.length === 0" class="py-16 text-center text-text-secondary">
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
