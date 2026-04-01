<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import {
  Bluetooth,
  Speaker,
  Usb,
  Radio,
  Wifi,
  Headphones,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  MoreVertical,
} from 'lucide-vue-next'
import { SbDropdown, SbDropdownItem } from '@/kit'
import { transportCmd } from '@/api/playback'
import type { DeviceSnapshot } from '@/api/types'

const props = defineProps<{
  device: DeviceSnapshot
  deviceIndex: number
}>()

const emit = defineEmits<{
  openDetail: [mac: string]
}>()

const { t } = useI18n()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()

const playerState = computed(() => props.device.player_state ?? 'OFFLINE')

const isStreaming = computed(
  () => props.device.audio_streaming || playerState.value === 'STREAMING',
)

const backendIconMap: Record<string, typeof Bluetooth> = {
  bluetooth_a2dp: Bluetooth,
  local_sink: Speaker,
  usb_audio: Usb,
  virtual_sink: Radio,
  snapcast_client: Wifi,
  vban: Wifi,
  le_audio: Headphones,
}

const BackendIcon = computed(
  () => backendIconMap[props.device.backend_info?.type ?? 'bluetooth_a2dp'] ?? Bluetooth,
)

function onVolumeUpdate(vol: number) {
  deviceStore.setVolume(props.device.mac, vol)
}

function onMuteUpdate(muted: boolean) {
  deviceStore.setMute(props.device.mac, muted)
}

async function onTransport(action: 'play' | 'pause' | 'previous' | 'next') {
  try {
    await transportCmd(action, props.deviceIndex)
  } catch {
    notifications.error(t('transport.failed'))
  }
}

async function onToggleEnabled() {
  try {
    await deviceStore.setEnabled(props.device.mac, !props.device.enabled)
  } catch {
    notifications.error(t('device.actions.enableFailed'))
  }
}
</script>

<template>
  <tr
    class="border-b border-border transition-colors hover:bg-surface-secondary"
    :class="{ 'opacity-50': !device.enabled }"
  >
    <!-- Name + icon -->
    <td class="py-2 pl-3 pr-2">
      <div class="flex items-center gap-2">
        <component :is="BackendIcon" class="h-4 w-4 shrink-0 text-text-secondary" />
        <button
          type="button"
          class="truncate text-sm font-medium text-text-primary hover:text-primary"
          @click="emit('openDetail', device.mac)"
        >
          {{ device.player_name }}
        </button>
      </div>
    </td>

    <!-- Status -->
    <td class="px-2 py-2">
      <DeviceStatusBadge :state="playerState" />
    </td>

    <!-- Volume + mute -->
    <td class="w-48 px-2 py-2">
      <VolumeSlider
        v-if="device.connected"
        :mac="device.mac"
        :volume="device.volume"
        :muted="device.muted"
        :disabled="!device.connected"
        @update:volume="onVolumeUpdate"
        @update:muted="onMuteUpdate"
      />
      <span v-else class="text-xs text-text-secondary">—</span>
    </td>

    <!-- Transport -->
    <td class="px-2 py-2">
      <div v-if="device.connected && isStreaming" class="flex items-center gap-0.5">
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
    </td>

    <!-- Adapter -->
    <td class="hidden px-2 py-2 text-xs text-text-secondary md:table-cell">
      {{ device.adapter ?? '—' }}
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
        <SbDropdownItem @click="deviceStore.reconnect(device.mac)">
          {{ t('device.actions.reconnect') }}
        </SbDropdownItem>
        <SbDropdownItem @click="deviceStore.standby(device.mac)">
          {{ t('device.actions.standby') }}
        </SbDropdownItem>
        <SbDropdownItem @click="deviceStore.wake(device.mac)">
          {{ t('device.actions.wake') }}
        </SbDropdownItem>
        <SbDropdownItem @click="onToggleEnabled">
          {{ device.enabled ? t('device.actions.disable') : t('device.actions.enable') }}
        </SbDropdownItem>
        <SbDropdownItem @click="emit('openDetail', device.mac)">
          {{ t('device.actions.details') }}
        </SbDropdownItem>
        <SbDropdownItem :destructive="true" @click="deviceStore.remove(device.mac)">
          {{ t('device.actions.remove') }}
        </SbDropdownItem>
      </SbDropdown>
    </td>
  </tr>
</template>
