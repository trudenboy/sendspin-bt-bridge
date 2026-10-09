<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNotificationStore } from '@/stores/notifications'
import { SbSlider } from '@/kit'
import { setVolume, setMute, setDevicePlayback } from '@/api/playback'
import { reconnectDevice } from '@/api/devices'
import {
  VolumeX,
  PauseCircle,
  RefreshCw,
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

async function onMuteAll() {
  actionLoading.value = true
  try {
    await Promise.allSettled(
      props.selectedNames.map((id) => setMute(id, true)),
    )
  } catch {
    notifications.error(t('devices.actionFailed'))
  } finally {
    actionLoading.value = false
  }
}

async function onPauseAll() {
  actionLoading.value = true
  try {
    await Promise.allSettled(
      props.selectedNames.map((id) => setDevicePlayback(id, 'pause')),
    )
  } catch {
    notifications.error(t('devices.actionFailed'))
  } finally {
    actionLoading.value = false
  }
}

async function onReconnectAll() {
  actionLoading.value = true
  try {
    await Promise.allSettled(props.selectedNames.map((id) => reconnectDevice(id)))
  } catch {
    notifications.error(t('devices.actionFailed'))
  } finally {
    actionLoading.value = false
  }
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
          class="h-4 w-4 cursor-pointer rounded border-border-strong text-primary accent-primary focus:ring-primary"
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
      <div class="flex items-center gap-1">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:opacity-50"
          :disabled="actionLoading"
          :title="t('devices.muteAll')"
          @click="onMuteAll"
        >
          <VolumeX class="h-4 w-4" />
          <span class="hidden sm:inline">{{ t('devices.muteAll') }}</span>
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:opacity-50"
          :disabled="actionLoading"
          :title="t('devices.pauseAll')"
          @click="onPauseAll"
        >
          <PauseCircle class="h-4 w-4" />
          <span class="hidden sm:inline">{{ t('devices.pauseAll') }}</span>
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:opacity-50"
          :disabled="actionLoading"
          :title="t('devices.reconnectAll')"
          @click="onReconnectAll"
        >
          <RefreshCw class="h-4 w-4" />
          <span class="hidden sm:inline">{{ t('devices.reconnectAll') }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
