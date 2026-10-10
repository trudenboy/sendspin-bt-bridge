<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useDeviceSelection } from '@/composables/useDeviceSelection'
import { SbFilterBar, SbButton, SbEmptyState, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceListRow from '@/components/devices/DeviceListRow.vue'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import GroupActionBar from '@/components/devices/GroupActionBar.vue'
import AddSpeakerDialog from '@/components/bluetooth/AddSpeakerDialog.vue'
import GuidanceBanner from '@/components/GuidanceBanner.vue'
import { Plus, LayoutGrid, List, ChevronDown } from 'lucide-vue-next'

type ViewMode = 'grid' | 'list'
const STORAGE_KEY = 'sb-view-mode'

const { t } = useI18n()
const bridge = useBridgeStore()
const deviceStore = useDeviceStore()

const drawerOpen = ref(false)
const selectedId = ref<string | null>(null)
const scanModalOpen = ref(false)
const btStore = useBluetoothStore()
watch(
  () => btStore.scanRequested,
  (requested) => {
    if (!requested) return
    scanModalOpen.value = true
    btStore.scanRequested = false
  },
  { immediate: true },
)
const viewMode = ref<ViewMode>(
  (localStorage.getItem(STORAGE_KEY) as ViewMode) || 'grid',
)

const selection = useDeviceSelection(
  computed(() => deviceStore.filteredDevices),
)

const showGroupBar = computed(() => bridge.devices.length >= 2)
/* Search and filters earn their space only on larger setups. */
const FILTER_FROM = 6
const showFilters = computed(() => bridge.devices.length >= FILTER_FROM)
const showViewToggle = computed(() => bridge.devices.length >= 4)

/** One line of context above the speakers: Music Assistant, playing, adapters. */
const playing = computed(() => bridge.devices.filter((d) => d.playback.playing && d.audio.streaming).length)
const connected = computed(() => bridge.devices.filter((d) => d.bluetooth.connected).length)
const selectable = computed(() => bridge.devices.length >= 2)


function setViewMode(mode: ViewMode) {
  viewMode.value = mode
  localStorage.setItem(STORAGE_KEY, mode)
}

/** Only the states some device is in right now, so every chip filters something. */
const statusFilters = computed(() => {
  const present = [...new Set(bridge.devices.map(deviceState))].sort()
  return present.map((key) => ({
    key,
    label: t(`device.status.${key}`),
    active: deviceStore.filter.status.includes(key),
  }))
})

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
    if (d.bluetooth.adapter.hci) set.add(d.bluetooth.adapter.hci)
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
  const g = bridge.groups.find((g) => g.id === deviceStore.filter.group)
  return g?.name ?? deviceStore.filter.group
})

function setGroupFilter(groupId: string) {
  deviceStore.filter.group = groupId
  if (groupId) {
    const group = bridge.groups.find((g) => g.id === groupId)
    if (group) {
      selection.selectGroup(group.members.map((m) => m.player_id))
    }
  }
}

function openDetail(id: string) {
  selectedId.value = id
  drawerOpen.value = true
}
</script>

<template>
  <div>
    <div class="mb-6 flex items-center justify-between gap-3">
      <h1 class="text-2xl font-semibold tracking-tight text-text-primary">
        {{ t('nav.home') }}
      </h1>
      <div class="flex items-center gap-2">
        <!-- View mode toggle -->
        <div v-if="showViewToggle" class="hidden rounded-lg border border-border sm:flex">
          <button
            type="button"
            class="rounded-l-lg p-2.5 transition-colors"
            :class="viewMode === 'grid' ? 'bg-primary-fill text-on-primary' : 'text-text-secondary hover:bg-surface-secondary'"
            :aria-label="t('devices.viewGrid')"
            @click="setViewMode('grid')"
          >
            <LayoutGrid class="h-4 w-4" />
          </button>
          <button
            type="button"
            class="rounded-r-lg p-2.5 transition-colors"
            :class="viewMode === 'list' ? 'bg-primary-fill text-on-primary' : 'text-text-secondary hover:bg-surface-secondary'"
            :aria-label="t('devices.viewList')"
            @click="setViewMode('list')"
          >
            <List class="h-4 w-4" />
          </button>
        </div>

        <SbButton size="sm" :aria-label="t('devices.addDevice')" @click="scanModalOpen = true">
          <template #icon-left>
            <Plus class="h-4 w-4" />
          </template>
          <span class="hidden sm:inline">{{ t('devices.addDevice') }}</span>
        </SbButton>
      </div>
    </div>

    <!-- Loading -->
    <!-- Placeholders keep the layout still while the first status arrives -->
    <div v-if="bridge.loading" class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-4" aria-busy="true" :aria-label="t('common.loading')">
      <div v-for="n in 3" :key="n" class="h-36 animate-pulse rounded-(--radius-card) border border-border bg-surface-card" />
    </div>

    <template v-else>
      <GuidanceBanner />

      <p v-if="bridge.devices.length" class="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-text-secondary">
        <span class="inline-flex items-center gap-1.5">
          <span class="size-2 rounded-full" :class="bridge.maConnected ? 'bg-success' : 'bg-text-disabled'" aria-hidden="true" />
          {{ bridge.maConnected ? t('home.maConnected') : t('home.maDisconnected') }}
        </span>
        <span>{{ t('home.connected', { n: connected, total: bridge.devices.length }) }}</span>
        <span v-if="playing">{{ t('home.playing', { n: playing }) }}</span>
      </p>

      <!-- Filter bar -->
      <div v-if="showFilters" class="mb-4 flex flex-wrap items-center gap-2">
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
              class="inline-flex cursor-pointer items-center gap-1 rounded-(--radius-button) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-secondary"
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
              class="inline-flex cursor-pointer items-center gap-1 rounded-(--radius-button) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-secondary"
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
            :key="group.id ?? ''"
            @click="setGroupFilter(group.id ?? '')"
          >
            {{ group.name }}
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
              <Plus class="h-4 w-4" />
            </template>
            {{ t('devices.addDevice') }}
          </SbButton>
        </template>
      </SbEmptyState>

      <!-- Device grid -->
      <div
        v-else-if="viewMode === 'grid' && deviceStore.filteredDevices.length > 0"
        class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-4"
      >
        <DeviceCard
          v-for="device in deviceStore.filteredDevices"
          :key="device.id"
          :device="device"
          :selectable="selectable"
          :selected="selection.selected.value.has(device.id)"
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
              v-for="device in deviceStore.filteredDevices"
              :key="device.id"
              :device="device"
              :selectable="selectable"
              :selected="selection.selected.value.has(device.id)"
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
      :device-id="selectedId"
      :open="drawerOpen"
      @update:open="drawerOpen = $event"
    />

    <!-- BT Scan modal -->
    <AddSpeakerDialog
      :open="scanModalOpen"
      @update:open="scanModalOpen = $event"
    />
  </div>
</template>
