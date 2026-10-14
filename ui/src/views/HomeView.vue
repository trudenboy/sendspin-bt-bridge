<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { deviceState, useDeviceStore, SORT_OPTIONS, type BackendFilter } from '@/stores/devices'
import { isPulseSink } from '@/utils/deviceBackend'
import { fleetSummary } from '@/utils/fleetSummary'
import { useAudioStore } from '@/stores/audio'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useDeviceSelection } from '@/composables/useDeviceSelection'
import { useRowExpansion } from '@/composables/useRowExpansion'
import { SbFilterBar, SbButton, SbEmptyState, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceListRow from '@/components/devices/DeviceListRow.vue'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import GroupActionBar from '@/components/devices/GroupActionBar.vue'
import AddSpeakerDialog from '@/components/bluetooth/AddSpeakerDialog.vue'
import GuidanceBanner from '@/components/GuidanceBanner.vue'
import DisabledPlayerCard from '@/components/devices/DisabledPlayerCard.vue'
import DisabledPlayerRow from '@/components/devices/DisabledPlayerRow.vue'
import { useMediaQuery } from '@/composables/useMediaQuery'
import { Plus, LayoutGrid, List, ChevronDown, ArrowUpDown } from 'lucide-vue-next'

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
/* Local audio outputs show their output's description; load the list once. */
const audio = useAudioStore()
let sinksRequested = false
watch(
  () => bridge.devices.some(isPulseSink),
  (hasOutput) => {
    if (!hasOutput || sinksRequested || audio.sinks.length) return
    sinksRequested = true
    void audio.fetchSinks()
  },
  { immediate: true },
)

/* Storage can be missing or throw (private windows, blocked site data). */
function readViewMode(): ViewMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'list' ? 'list' : 'grid'
  } catch {
    return 'grid'
  }
}
const viewMode = ref<ViewMode>(readViewMode())
/* The list's columns need a tablet's width; on a phone it is always the grid (the choice is kept). */
const wide = useMediaQuery('(min-width: 640px)')
const shownView = computed<ViewMode>(() => (wide.value ? viewMode.value : 'grid'))

/* Running and disabled players alike: the dashboard shows both. */
const playerCount = computed(() => bridge.devices.length + deviceStore.disabledPlayers.length)

const selection = useDeviceSelection(
  computed(() => deviceStore.filteredDevices),
  computed(() => deviceStore.filteredPlayers.filter((p) => p.kind === 'disabled').map((p) => p.id)),
)
const selectedDisabled = computed(() =>
  deviceStore.disabledPlayers.filter((p) => selection.selectedDisabledIds.value.includes(p.id)),
)

const showGroupBar = computed(() => playerCount.value >= 2)
/* Search and filters earn their space only on larger setups. */
const FILTER_FROM = 6
const showFilters = computed(() => playerCount.value >= FILTER_FROM)
const showViewToggle = computed(() => playerCount.value >= 1)

/** One line of context above the speakers: Music Assistant, playing, adapters. */
const fleet = computed(() => fleetSummary(bridge.devices))
const selectable = computed(() => playerCount.value >= 2)


function setViewMode(mode: ViewMode) {
  viewMode.value = mode
  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    /* not remembered */
  }
}

/* Order (both views) and, on mixed setups, which kind of player to show. */
const showSort = computed(() => playerCount.value >= 2)
const mixedBackends = computed(() => {
  const outputs = bridge.devices.some(isPulseSink) || deviceStore.disabledPlayers.some((p) => p.output)
  const speakers = bridge.devices.some((d) => !isPulseSink(d)) || deviceStore.disabledPlayers.some((p) => !p.output)
  return outputs && speakers
})
/* The filter lives in the store and outlives this view: clear it on return too. */
watch(
  mixedBackends,
  (mixed) => {
    if (!mixed) deviceStore.filter.backend = ''
  },
  { immediate: true },
)
const BACKEND_OPTIONS: { value: BackendFilter; label: string }[] = [
  { value: '', label: 'devices.backendFilter.all' },
  { value: 'bluetooth', label: 'devices.backendFilter.bluetooth' },
  { value: 'output', label: 'devices.backendFilter.output' },
]
const backendLabel = computed(() =>
  t(BACKEND_OPTIONS.find((o) => o.value === deviceStore.filter.backend)?.label ?? 'devices.backendFilter.all'),
)
const triggerClass =
  'inline-flex cursor-pointer items-center gap-1 rounded-(--radius-button) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-secondary'

/* List view: one expanded row, the first streaming one on load. */
const expansion = useRowExpansion(computed(() => deviceStore.filteredDevices))

/** Only the states some device is in right now, so every chip filters something. */
const statusFilters = computed(() => {
  const states = bridge.devices.map(deviceState)
  if (deviceStore.disabledPlayers.length) states.push('disabled')
  const present = [...new Set(states)].sort()
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
    if (d.bluetooth?.adapter.hci) set.add(d.bluetooth.adapter.hci)
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
            :aria-pressed="viewMode === 'grid' ? 'true' : 'false'"
            @click="setViewMode('grid')"
          >
            <LayoutGrid class="h-4 w-4" />
          </button>
          <button
            type="button"
            class="rounded-r-lg p-2.5 transition-colors"
            :class="viewMode === 'list' ? 'bg-primary-fill text-on-primary' : 'text-text-secondary hover:bg-surface-secondary'"
            :aria-label="t('devices.viewList')"
            :aria-pressed="viewMode === 'list' ? 'true' : 'false'"
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

      <!-- From md up the header's runtime row carries these. -->
      <p v-if="bridge.devices.length" data-home-summary class="mb-4 flex md:hidden flex-wrap items-center gap-x-4 gap-y-1 text-sm text-text-secondary">
        <span class="inline-flex items-center gap-1.5">
          <span class="size-2 rounded-full" :class="bridge.maConnected ? 'bg-success' : 'bg-text-disabled'" aria-hidden="true" />
          {{ bridge.maConnected ? t('home.maConnected') : t('home.maDisconnected') }}
        </span>
        <span>{{ t('home.connected', { n: fleet.connected, total: fleet.total }) }}</span>
        <span v-if="fleet.playing">{{ t('home.playing', { n: fleet.playing }) }}</span>
      </p>

      <!-- Filter bar, order and backend -->
      <div v-if="showFilters || showSort" class="mb-4 flex flex-wrap items-center gap-2">
        <SbFilterBar
          v-if="showFilters"
          v-model="deviceStore.filter.search"
          :placeholder="t('common.search')"
          :filters="statusFilters"
          class="flex-1"
          @toggle-filter="onToggleFilter"
        />

        <!-- Adapter filter dropdown -->
        <SbDropdown v-if="showFilters && uniqueAdapters.length > 1" align="right">
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
        <SbDropdown v-if="showFilters && bridge.groups.length > 0" align="right">
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

        <SbDropdown v-if="mixedBackends" align="right">
          <template #trigger>
            <button
              type="button"
              data-backend-filter
              aria-haspopup="true"
              :aria-label="`${t('devices.backendFilter.label')}: ${backendLabel}`"
              :class="triggerClass"
            >
              {{ backendLabel }}
              <ChevronDown class="h-4 w-4" aria-hidden="true" />
            </button>
          </template>
          <SbDropdownItem
            v-for="option in BACKEND_OPTIONS"
            :key="option.value"
            :data-backend-option="option.value || 'all'"
            @click="deviceStore.filter.backend = option.value"
          >
            <span :class="deviceStore.filter.backend === option.value ? 'font-medium text-primary-text' : ''">{{ t(option.label) }}</span>
          </SbDropdownItem>
        </SbDropdown>

        <SbDropdown v-if="showSort" align="right" :class="showFilters ? '' : 'ml-auto'">
          <template #trigger>
            <button
              type="button"
              data-sort
              aria-haspopup="true"
              :aria-label="`${t('devices.sort.label')}: ${t(`devices.sort.${deviceStore.sortBy}`)}`"
              :class="triggerClass"
            >
              <ArrowUpDown class="h-4 w-4 text-text-secondary" aria-hidden="true" />
              {{ t(`devices.sort.${deviceStore.sortBy}`) }}
              <ChevronDown class="h-4 w-4" aria-hidden="true" />
            </button>
          </template>
          <SbDropdownItem
            v-for="option in SORT_OPTIONS"
            :key="option"
            :data-sort-option="option"
            @click="deviceStore.setSort(option)"
          >
            <span :class="deviceStore.sortBy === option ? 'font-medium text-primary-text' : ''">{{ t(`devices.sort.${option}`) }}</span>
          </SbDropdownItem>
        </SbDropdown>
      </div>

      <!-- Group action bar -->
      <GroupActionBar
        v-if="showGroupBar"
        :devices="deviceStore.filteredDevices"
        :total="deviceStore.filteredPlayers.length"
        :selected-count="selection.selectedCount.value"
        :all-selected="selection.allSelected.value"
        :some-selected="selection.someSelected.value"
        :selected-devices="selection.selectedDevices.value"
        :selected-names="selection.selectedNames.value"
        :selected-disabled="selectedDisabled"
        @toggle-all="selection.toggleAll()"
      />

      <!-- Empty state -->
      <SbEmptyState
        v-if="playerCount === 0"
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
        v-else-if="shownView === 'grid' && deviceStore.filteredPlayers.length > 0"
        class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-4"
      >
        <template v-for="entry in deviceStore.filteredPlayers" :key="entry.id">
          <DeviceCard
            v-if="entry.kind === 'device'"
            :device="entry.device"
            :selectable="selectable"
            :selected="selection.selected.value.has(entry.id)"
            @open-detail="openDetail"
            @toggle-select="selection.toggle($event)"
          />
          <DisabledPlayerCard
            v-else
            :player="entry.player"
            :selectable="selectable"
            :selected="selection.selected.value.has(entry.id)"
            @open-detail="openDetail"
            @toggle-select="selection.toggle($event)"
          />
        </template>
      </div>

      <!-- Device list -->
      <div
        v-else-if="shownView === 'list' && deviceStore.filteredPlayers.length > 0"
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
            <template v-for="entry in deviceStore.filteredPlayers" :key="entry.id">
              <DeviceListRow
                v-if="entry.kind === 'device'"
                :device="entry.device"
                :selectable="selectable"
                :selected="selection.selected.value.has(entry.id)"
                :expanded="expansion.expandedId.value === entry.id"
                @open-detail="openDetail"
                @toggle-select="selection.toggle($event)"
                @toggle-expand="expansion.toggle($event)"
              />
              <DisabledPlayerRow
                v-else
                :player="entry.player"
                :selectable="selectable"
                :selected="selection.selected.value.has(entry.id)"
                @open-detail="openDetail"
                @toggle-select="selection.toggle($event)"
              />
            </template>
          </tbody>
        </table>
      </div>

      <!-- No filter results -->
      <div v-else-if="deviceStore.filteredPlayers.length === 0" class="py-16 text-center text-text-secondary">
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
