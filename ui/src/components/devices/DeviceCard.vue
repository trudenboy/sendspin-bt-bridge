<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import SpeakerActionsMenu from './SpeakerActionsMenu.vue'
import { speakerName } from '@/utils/speakerName'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import PlaybackProgress from '@/components/playback/PlaybackProgress.vue'
import {
  Music,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  BatteryLow,
  BatteryMedium,
  BatteryFull,
  Signal,
  SignalMedium,
  SignalLow,
} from 'lucide-vue-next'
import { transport } from '@/api/playback'
import type { Device } from '@/api/types'
import type { MaNowPlaying } from '@/composables/usePlaybackProgress'

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

const transportLoading = ref(false)

const state = computed(() => deviceState(props.device))
const displayName = computed(() => speakerName(props.device.name, props.device.bluetooth.mac ?? props.device.id))
const room = computed(() => props.device.room?.name || null)

/** Signal strength in words; the dBm value is in the tooltip. */
const signal = computed(() => {
  const rssi = props.device.bluetooth.rssi_dbm
  if (rssi == null || !props.device.bluetooth.connected) return null
  if (rssi >= -60) return { level: 'strong', icon: Signal }
  if (rssi >= -75) return { level: 'fair', icon: SignalMedium }
  return { level: 'weak', icon: SignalLow }
})
const connected = computed(() => props.device.bluetooth.connected)
const released = computed(() => !props.device.bluetooth.management_enabled)
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
  if (level < 20) return 'text-error'
  if (level <= 50) return 'text-warning'
  return 'text-success'
})

const isStreaming = computed(() => props.device.playback.playing && props.device.audio.streaming)

/** Title/artist: the daemon's track metadata, else Music Assistant's now-playing. */
const nowPlaying = computed(() => {
  const track = props.device.playback.track
  if (track.title) return { title: track.title, artist: track.artist ?? null }
  const np = props.device.music_assistant.now_playing as MaNowPlaying | null
  if (np?.track) return { title: String(np.track), artist: np.artist ? String(np.artist) : null }
  return null
})

const supports = (command: string) => {
  const list = props.device.playback.supported_commands ?? []
  return list.length === 0 || list.includes(command)
}

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




function onDetails() {
  emit('openDetail', props.device.id)
}






async function onTransport(command: 'play' | 'pause' | 'previous' | 'next') {
  transportLoading.value = true
  try {
    await transport(props.device.id, command)
  } catch {
    notifications.error(t('transport.failed'))
  } finally {
    transportLoading.value = false
  }
}
</script>

<template>
  <!-- The name is a button stretched over the card, so the whole card opens
       the details; controls sit above it (relative z-10). -->
  <article
    class="relative flex flex-col gap-3 rounded-(--radius-card) border bg-surface-card p-4 transition-colors"
    :class="[
      selected ? 'border-primary ring-1 ring-primary' : 'border-border hover:border-border-strong',
      !device.enabled ? 'opacity-60' : '',
    ]"
  >
    <header class="flex items-start gap-2">
      <input
        v-if="selectable"
        type="checkbox"
        :checked="selected"
        class="relative z-10 mt-1 size-4 shrink-0 cursor-pointer accent-primary"
        :aria-label="t('device.select', { name: displayName })"
        @change="emit('toggleSelect', device.id)"
      />
      <div class="min-w-0 flex-1">
        <button
          type="button"
          class="block max-w-full text-left text-base font-medium leading-snug text-text-primary after:absolute after:inset-0 after:rounded-(--radius-card) after:content-[''] focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-primary/60"
          :title="device.name ?? undefined"
          @click="onDetails"
        >
          <span class="line-clamp-2 break-words">{{ displayName }}</span>
        </button>
        <p class="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-text-secondary">
          <span v-if="room">{{ room }}</span>
          <span v-if="battery != null && BatteryIcon" class="inline-flex items-center gap-0.5" :class="batteryColor" :title="t('device.battery', { level: battery })">
            <component :is="BatteryIcon" class="size-3.5" aria-hidden="true" />{{ battery }}%
          </span>
          <span v-if="signal" class="inline-flex items-center gap-0.5" :title="t('device.signal', { dbm: device.bluetooth.rssi_dbm })">
            <component :is="signal.icon" class="size-3.5" aria-hidden="true" />{{ t(`device.signalLevel.${signal.level}`) }}
          </span>
          <span v-if="released" class="text-warning">{{ t('bluetooth.released') }}</span>
        </p>
      </div>
      <DeviceStatusBadge :state="state" class="relative z-10 shrink-0" />
      <div class="relative z-10 -mt-1.5 -mr-2"><SpeakerActionsMenu :device="device" /></div>
    </header>

    <!-- Now playing with its main control -->
    <div v-if="nowPlaying && connected" class="relative z-10 flex items-center gap-3 rounded-(--radius-button) bg-surface-secondary px-3 py-2">
      <Music class="size-4 shrink-0 text-primary-text" aria-hidden="true" />
      <div class="min-w-0 flex-1">
        <p class="truncate text-sm font-medium text-text-primary">{{ nowPlaying.title }}</p>
        <p v-if="nowPlaying.artist" class="truncate text-xs text-text-secondary">{{ nowPlaying.artist }}</p>
      </div>
      <button
        v-if="isStreaming && supports('previous')"
        type="button"
        class="inline-flex size-9 shrink-0 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-card hover:text-text-primary"
        :aria-label="t('transport.previous')"
        :disabled="transportLoading"
        @click="onTransport('previous')"
      >
        <SkipBack class="size-4" />
      </button>
      <button
        v-if="supports('pause') || supports('play')"
        type="button"
        class="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-fill text-on-primary transition-colors hover:bg-primary-dark disabled:opacity-50"
        :aria-label="isStreaming ? t('transport.pause') : t('transport.play')"
        :disabled="transportLoading"
        @click="onTransport(isStreaming ? 'pause' : 'play')"
      >
        <Pause v-if="isStreaming" class="size-5" />
        <Play v-else class="size-5" />
      </button>
      <button
        v-if="isStreaming && supports('next')"
        type="button"
        class="inline-flex size-9 shrink-0 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-card hover:text-text-primary"
        :aria-label="t('transport.next')"
        :disabled="transportLoading"
        @click="onTransport('next')"
      >
        <SkipForward class="size-4" />
      </button>
    </div>
    <PlaybackProgress v-if="isStreaming" :device="device" class="relative z-10" />

    <VolumeSlider
      v-if="connected"
      class="relative z-10"
      :mac="device.bluetooth.mac ?? device.id"
      :name="displayName"
      :volume="device.audio.volume"
      :muted="device.audio.muted"
      :disabled="!device.audio.has_sink"
      @update:volume="onVolumeUpdate"
      @update:muted="onMuteUpdate"
    />

  </article>
</template>
