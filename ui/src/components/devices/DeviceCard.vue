<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDeviceStore } from '@/stores/devices'
import { useMaStore } from '@/stores/ma'
import {
  SbCard,
  SbBadge,
  SbDropdown,
  SbDropdownItem,
} from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import {
  Bluetooth,
  Speaker,
  Usb,
  Radio,
  Wifi,
  Headphones,
  MoreVertical,
  Music,
} from 'lucide-vue-next'
import type { DeviceSnapshot } from '@/api/types'

const props = defineProps<{
  device: DeviceSnapshot
}>()

const emit = defineEmits<{
  openDetail: [mac: string]
}>()

const { t } = useI18n()
const deviceStore = useDeviceStore()
const maStore = useMaStore()

const playerState = computed(() => props.device.player_state ?? 'OFFLINE')

const backendLabel = computed(() => {
  const type = props.device.backend_info?.type
  return type ? t(`device.backend.${type}`) : t('device.backend.bluetooth_a2dp')
})

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

const nowPlaying = computed(() => {
  for (const np of Object.values(maStore.nowPlaying)) {
    if (np.state === 'playing') return np
  }
  return null
})

function onVolumeUpdate(vol: number) {
  deviceStore.setVolume(props.device.mac, vol)
}

function onMuteUpdate(muted: boolean) {
  deviceStore.setMute(props.device.mac, muted)
}

function onReconnect() {
  deviceStore.reconnect(props.device.mac)
}

function onStandby() {
  deviceStore.standby(props.device.mac)
}

function onWake() {
  deviceStore.wake(props.device.mac)
}

function onDetails() {
  emit('openDetail', props.device.mac)
}

function onRemove() {
  deviceStore.remove(props.device.mac)
}
</script>

<template>
  <SbCard>
    <template #header>
      <div class="flex min-w-0 flex-1 items-center gap-2">
        <component :is="BackendIcon" class="h-4 w-4 shrink-0 text-text-secondary" />
        <span class="truncate font-semibold text-text-primary">
          {{ device.player_name }}
        </span>
      </div>
    </template>

    <template #actions>
      <div class="flex items-center gap-2">
        <DeviceStatusBadge :state="playerState" />
        <SbDropdown align="right">
          <template #trigger>
            <button
              type="button"
              class="cursor-pointer rounded p-1 text-text-secondary transition-colors hover:text-text-primary"
              :aria-label="t('device.actions.details')"
            >
              <MoreVertical class="h-4 w-4" />
            </button>
          </template>
          <SbDropdownItem @click="onReconnect">
            {{ t('device.actions.reconnect') }}
          </SbDropdownItem>
          <SbDropdownItem @click="onStandby">
            {{ t('device.actions.standby') }}
          </SbDropdownItem>
          <SbDropdownItem @click="onWake">
            {{ t('device.actions.wake') }}
          </SbDropdownItem>
          <SbDropdownItem @click="onDetails">
            {{ t('device.actions.details') }}
          </SbDropdownItem>
          <SbDropdownItem :destructive="true" @click="onRemove">
            {{ t('device.actions.remove') }}
          </SbDropdownItem>
        </SbDropdown>
      </div>
    </template>

    <!-- Body -->
    <div class="space-y-3">
      <!-- Backend type badge -->
      <div class="flex items-center gap-2">
        <SbBadge tone="neutral" size="sm">
          {{ backendLabel }}
        </SbBadge>
        <span class="text-xs text-text-secondary">{{ device.mac }}</span>
      </div>

      <!-- Volume slider -->
      <VolumeSlider
        v-if="device.connected"
        :mac="device.mac"
        :volume="device.volume"
        :muted="device.muted"
        :disabled="!device.connected"
        @update:volume="onVolumeUpdate"
        @update:muted="onMuteUpdate"
      />

      <!-- Now playing -->
      <div
        v-if="nowPlaying && device.audio_streaming"
        class="flex items-center gap-2 rounded-lg bg-surface-secondary p-2"
      >
        <Music class="h-4 w-4 shrink-0 text-primary" />
        <div class="min-w-0">
          <p class="truncate text-sm font-medium text-text-primary">
            {{ nowPlaying.title }}
          </p>
          <p v-if="nowPlaying.artist" class="truncate text-xs text-text-secondary">
            {{ nowPlaying.artist }}
          </p>
        </div>
      </div>
    </div>
  </SbCard>
</template>
