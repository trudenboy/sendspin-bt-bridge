<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import PlaybackProgress from '@/components/playback/PlaybackProgress.vue'
import {
  Bluetooth,
  Pause,
  SkipBack,
  SkipForward,
  MoreVertical,
  BatteryLow,
  BatteryMedium,
  BatteryFull,
} from 'lucide-vue-next'
import { SbDropdown, SbDropdownItem } from '@/kit'
import { transport } from '@/api/playback'
import type { Device } from '@/api/types'

const props = withDefaults(
  defineProps<{
    device: Device
    selectable?: boolean
    selected?: boolean
  }>(),
  {
    selectable: false,
    selected: false,
  },
)

const emit = defineEmits<{
  openDetail: [id: string]
  toggleSelect: [id: string]
}>()

const { t } = useI18n()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()

const state = computed(() => deviceState(props.device))
const connected = computed(() => props.device.bluetooth.connected)
const isStreaming = computed(() => props.device.playback.playing && props.device.audio.streaming)
const battery = computed(() => props.device.bluetooth.battery_level ?? null)

const BatteryIcon = computed(() => {
  const level = battery.value
  if (level == null) return null
  if (level < 20) return BatteryLow
  if (level <= 50) return BatteryMedium
  return BatteryFull
})

const batteryColor = computed(() => {
  const level = battery.value
  if (level == null) return ''
  if (level < 20) return 'text-red-500'
  if (level <= 50) return 'text-yellow-500'
  return 'text-green-500'
})

async function run(action: () => Promise<unknown>, failure: string) {
  try {
    await action()
  } catch {
    notifications.error(t(failure))
  }
}

function onVolumeUpdate(level: number) {
  void run(() => deviceStore.setVolume(props.device.id, level), 'device.actions.enableFailed')
}

function onMuteUpdate(muted: boolean) {
  void run(() => deviceStore.setMute(props.device.id, muted), 'device.actions.enableFailed')
}

function onTransport(command: 'play' | 'pause' | 'previous' | 'next') {
  void run(() => transport(props.device.id, command), 'transport.failed')
}

function onToggleEnabled() {
  void run(() => deviceStore.setEnabled(props.device.id, !props.device.enabled), 'device.actions.enableFailed')
}

function onForget() {
  if (!window.confirm(t('device.actions.forgetConfirm'))) return
  void run(() => deviceStore.forget(props.device.id), 'device.actions.enableFailed')
}
</script>

<template>
  <tr
    class="border-b border-border transition-colors hover:bg-surface-secondary"
    :class="{ 'opacity-50': !device.enabled }"
  >
    <!-- Checkbox -->
    <td v-if="selectable" class="w-10 py-2 pl-3 pr-1">
      <input
        type="checkbox"
        :checked="selected"
        class="h-4 w-4 cursor-pointer rounded border-gray-300 text-primary accent-primary focus:ring-primary"
        :aria-label="`Select ${device.name}`"
        @click.stop
        @change.stop="emit('toggleSelect', device.id)"
      />
    </td>

    <!-- Name + icon -->
    <td class="py-2 pl-3 pr-2">
      <div class="flex items-center gap-2">
        <Bluetooth class="h-4 w-4 shrink-0 text-text-secondary" />
        <button
          type="button"
          class="truncate text-sm font-medium text-text-primary hover:text-primary"
          @click="emit('openDetail', device.id)"
        >
          {{ device.name }}
        </button>
        <span
          v-if="battery != null && BatteryIcon"
          class="ml-auto flex shrink-0 items-center gap-0.5 text-xs"
          :class="batteryColor"
          :title="t('device.battery', { level: battery })"
        >
          <component :is="BatteryIcon" class="h-3 w-3" />
          {{ battery }}%
        </span>
      </div>
    </td>

    <!-- Status -->
    <td class="px-2 py-2">
      <DeviceStatusBadge :state="state" />
    </td>

    <!-- Volume + mute -->
    <td class="w-48 px-2 py-2">
      <VolumeSlider
        v-if="connected"
        :mac="device.bluetooth.mac ?? device.id"
        :volume="device.audio.volume"
        :muted="device.audio.muted"
        :disabled="!device.audio.has_sink"
        @update:volume="onVolumeUpdate"
        @update:muted="onMuteUpdate"
      />
      <span v-else class="text-xs text-text-secondary">—</span>
    </td>

    <!-- Transport -->
    <td class="px-2 py-2">
      <div v-if="connected && isStreaming" class="space-y-1">
        <div class="flex items-center gap-0.5">
          <button
            type="button"
            class="rounded p-1 text-text-secondary hover:text-text-primary"
            :aria-label="t('transport.previous')"
            @click="onTransport('previous')"
          >
            <SkipBack class="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            class="rounded p-1 text-primary hover:text-primary/80"
            :aria-label="t('transport.pause')"
            @click="onTransport('pause')"
          >
            <Pause class="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            class="rounded p-1 text-text-secondary hover:text-text-primary"
            :aria-label="t('transport.next')"
            @click="onTransport('next')"
          >
            <SkipForward class="h-3.5 w-3.5" />
          </button>
        </div>
        <PlaybackProgress :device="device" slim />
      </div>
    </td>

    <!-- Adapter -->
    <td class="hidden px-2 py-2 text-xs text-text-secondary md:table-cell">
      {{ device.bluetooth.adapter.hci || '—' }}
    </td>

    <!-- Actions -->
    <td class="py-2 pl-2 pr-3 text-right">
      <SbDropdown align="right">
        <template #trigger>
          <button
            type="button"
            class="cursor-pointer rounded p-1 text-text-secondary hover:text-text-primary"
            :aria-label="t('device.actions.details')"
          >
            <MoreVertical class="h-4 w-4" />
          </button>
        </template>
        <SbDropdownItem @click="deviceStore.reconnect(device.id)">
          {{ t('device.actions.reconnect') }}
        </SbDropdownItem>
        <SbDropdownItem @click="deviceStore.standby(device.id)">
          {{ t('device.actions.standby') }}
        </SbDropdownItem>
        <SbDropdownItem @click="deviceStore.wake(device.id)">
          {{ t('device.actions.wake') }}
        </SbDropdownItem>
        <SbDropdownItem @click="onToggleEnabled">
          {{ device.enabled ? t('device.actions.disable') : t('device.actions.enable') }}
        </SbDropdownItem>
        <SbDropdownItem @click="emit('openDetail', device.id)">
          {{ t('device.actions.details') }}
        </SbDropdownItem>
        <SbDropdownItem :destructive="true" @click="onForget">
          {{ t('device.actions.forget') }}
        </SbDropdownItem>
      </SbDropdown>
    </td>
  </tr>
</template>
