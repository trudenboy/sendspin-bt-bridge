<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNotificationStore } from '@/stores/notifications'
import { useDeviceStore } from '@/stores/devices'
import { SbSlider } from '@/kit'
import { setVolume, setMute, setDevicePlayback } from '@/api/playback'
import { reconnectDevice } from '@/api/devices'
import { confirmDialog } from '@/composables/useConfirm'
import { bulkEligible, type BulkAction } from '@/utils/bulkActions'
import { speakerName } from '@/utils/speakerName'
import { canDo } from '@/utils/supportedActions'
import { canMute, canPause } from '@/utils/bulkActions'
import {
  VolumeX,
  PauseCircle,
  RefreshCw,
  Moon,
  PowerOff,
  LogOut,
} from 'lucide-vue-next'
import type { Device } from '@/api/types'

const props = defineProps<{
  devices: Device[]
  total: number
  selectedCount: number
  allSelected: boolean
  someSelected: boolean
  selectedDevices: Device[]
  /** Selected device ids. */
  selectedNames: string[]
}>()

const emit = defineEmits<{
  toggleAll: []
}>()

const { t } = useI18n()
const notifications = useNotificationStore()
const deviceStore = useDeviceStore()

const groupVolume = ref(50)
const actionLoading = ref(false)
let debounceTimer: ReturnType<typeof setTimeout> | null = null

const averageVolume = computed(() => {
  if (props.selectedDevices.length === 0) return 50
  const sum = props.selectedDevices.reduce((acc, d) => acc + d.audio.volume, 0)
  return Math.round(sum / props.selectedDevices.length)
})

watch(
  () => props.selectedNames.join(','),
  () => {
    groupVolume.value = averageVolume.value
  },
)

function onGroupVolumeChange(vol: number) {
  groupVolume.value = vol
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    applyGroupVolume(vol)
  }, 300)
}

async function applyGroupVolume(vol: number) {
  const names = props.selectedNames
  await Promise.allSettled(names.map((id) => setVolume(id, vol)))
}

const nameOf = (d: Device) => speakerName(d.name, d.bluetooth?.mac ?? d.id)

/** Run one command on each target and name every speaker it failed for in one toast. */
async function runEach(targets: Device[], label: string, run: (id: string) => Promise<unknown>) {
  if (!targets.length) return
  actionLoading.value = true
  try {
    const results = await Promise.allSettled(targets.map((d) => run(d.id)))
    const failed = targets.filter((_, i) => results[i]!.status === 'rejected').map(nameOf)
    if (failed.length) notifications.error(t('devices.bulk.failed', { action: label, names: failed.join(', ') }))
  } finally {
    actionLoading.value = false
  }
}

const reconnectable = computed(() => props.selectedDevices.filter((d) => canDo(d, 'reconnect')))
/* Only speakers that can take the command: the bridge answers 409 for the rest. */
const mutable = computed(() => props.selectedDevices.filter(canMute))
const pausable = computed(() => props.selectedDevices.filter(canPause))

function onMuteAll() {
  return runEach(mutable.value, t('devices.muteAll'), (id) => setMute(id, true))
}

function onPauseAll() {
  return runEach(pausable.value, t('devices.pauseAll'), (id) => setDevicePlayback(id, 'pause'))
}

function onReconnectAll() {
  return runEach(reconnectable.value, t('devices.reconnectAll'), (id) => reconnectDevice(id))
}

/* Power save, standby and hand-over: only for the selected devices that can take them. */
const BULK: { action: BulkAction; icon: typeof Moon; confirm: boolean }[] = [
  { action: 'power_save', icon: Moon, confirm: false },
  { action: 'standby', icon: PowerOff, confirm: true },
  { action: 'release', icon: LogOut, confirm: true },
]

const eligible = computed(() =>
  Object.fromEntries(
    BULK.map(({ action }) => [action, props.selectedDevices.filter((d) => bulkEligible(d, action))]),
  ) as Record<BulkAction, Device[]>,
)

const bulkButtons = computed(() => BULK.filter(({ action }) => eligible.value[action].length > 0))

function runOne(action: BulkAction, id: string): Promise<unknown> {
  if (action === 'power_save') return deviceStore.setPowerSave(id, true)
  if (action === 'standby') return deviceStore.standby(id)
  return deviceStore.release(id, true)
}

async function onBulk(action: BulkAction, ask: boolean) {
  const targets = eligible.value[action]
  if (!targets.length) return
  if (ask) {
    const names = targets.map(nameOf).join(', ')
    const ok = await confirmDialog({
      title: t(`devices.bulk.${action}.confirmTitle`, { count: targets.length }),
      message: t(`devices.bulk.${action}.confirmMessage`, { names }),
      confirmLabel: t(`devices.bulk.${action}.label`),
    })
    if (!ok) return
  }
  await runEach(targets, t(`devices.bulk.${action}.label`), (id) => runOne(action, id))
}

const selectionLabel = computed(() => {
  if (props.selectedCount === 0) return t('devices.noneSelected')
  return t('devices.selected', {
    count: props.selectedCount,
    total: props.total,
  })
})

const checkboxRef = ref<HTMLInputElement | null>(null)

watch(
  () => props.someSelected,
  (indeterminate) => {
    if (checkboxRef.value) {
      checkboxRef.value.indeterminate = indeterminate
    }
  },
  { flush: 'post' },
)
</script>

<template>
  <div class="mb-4 rounded-lg border border-border bg-surface-card p-3">
    <!-- Row 1: Selection -->
    <div class="flex items-center gap-3">
      <label class="flex cursor-pointer items-center gap-2">
        <input
          ref="checkboxRef"
          type="checkbox"
          :checked="allSelected"
          class="h-4 w-4 cursor-pointer rounded border-border-strong text-primary-text accent-primary focus:ring-primary"
          :aria-label="t('devices.selectAll')"
          @change="emit('toggleAll')"
        />
        <span class="text-sm text-text-secondary">
          {{ selectionLabel }}
        </span>
      </label>
    </div>

    <!-- Row 2: Actions (visible when any selected) -->
    <div
      v-if="selectedCount > 0"
      class="mt-3 flex flex-wrap items-center gap-3 border-t border-border pt-3"
    >
      <!-- Group Volume Slider -->
      <div class="flex min-w-[200px] flex-1 items-center gap-3">
        <span class="shrink-0 text-sm font-medium text-text-secondary">
          {{ t('devices.groupVolume') }}
        </span>
        <SbSlider
          :model-value="groupVolume"
          :min="0"
          :max="100"
          :step="1"
          :show-value="true"
          class="flex-1"
          @update:model-value="onGroupVolumeChange"
        />
      </div>

      <!-- Action buttons -->
      <div class="flex flex-wrap items-center gap-1">
        <button
          v-if="mutable.length"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:opacity-50"
          :disabled="actionLoading"
          data-bulk="mute"
          :title="t('devices.muteAll')"
          @click="onMuteAll"
        >
          <VolumeX class="h-4 w-4" />
          <span class="hidden sm:inline">{{ t('devices.muteAll') }}</span>
        </button>
        <button
          v-if="pausable.length"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:opacity-50"
          :disabled="actionLoading"
          data-bulk="pause"
          :title="t('devices.pauseAll')"
          @click="onPauseAll"
        >
          <PauseCircle class="h-4 w-4" />
          <span class="hidden sm:inline">{{ t('devices.pauseAll') }}</span>
        </button>
        <button
          v-if="reconnectable.length"
          type="button"
          data-bulk="reconnect"
          class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:opacity-50"
          :disabled="actionLoading"
          :title="t('devices.reconnectAll')"
          @click="onReconnectAll"
        >
          <RefreshCw class="h-4 w-4" />
          <span class="hidden sm:inline">{{ t('devices.reconnectAll') }}</span>
        </button>
        <button
          v-for="b in bulkButtons"
          :key="b.action"
          type="button"
          :data-bulk="b.action"
          class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:opacity-50"
          :disabled="actionLoading"
          :title="t(`devices.bulk.${b.action}.title`, { count: eligible[b.action].length, total: selectedCount })"
          :aria-label="t(`devices.bulk.${b.action}.label`)"
          @click="onBulk(b.action, b.confirm)"
        >
          <component :is="b.icon" class="h-4 w-4" aria-hidden="true" />
          <span class="hidden sm:inline">{{ t(`devices.bulk.${b.action}.label`) }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
