<script setup lang="ts">
import { confirmDialog } from '@/composables/useConfirm'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { SbCard, SbBadge, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import PlaybackProgress from '@/components/playback/PlaybackProgress.vue'
import BtDeviceInfoModal from '@/components/bluetooth/BtDeviceInfoModal.vue'
import {
  Bluetooth,
  MoreVertical,
  Music,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  BatteryLow,
  BatteryMedium,
  BatteryFull,
  Anchor,
} from 'lucide-vue-next'
import { claimAudio } from '@/api/devices'
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
const btInfoOpen = ref(false)

const state = computed(() => deviceState(props.device))
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

async function onReconnect() {
  const job = await deviceStore.reconnect(props.device.id).catch(() => null)
  if (!job || job.status !== 'succeeded') notifications.error(t('device.actions.reconnectFailed'))
}

function onStandby() {
  void run(() => deviceStore.standby(props.device.id), 'device.actions.enableFailed')
}

function onWake() {
  void run(() => deviceStore.wake(props.device.id), 'device.actions.enableFailed')
}

function onDetails() {
  emit('openDetail', props.device.id)
}

async function onForget() {
  const ok = await confirmDialog({
    title: t('device.actions.forgetTitle', { name: props.device.name }),
    message: t('device.actions.forgetConfirm'),
    confirmLabel: t('device.actions.forget'),
    danger: true,
  })
  if (!ok) return
  void run(() => deviceStore.forget(props.device.id), 'device.actions.enableFailed')
}

function onToggleEnabled() {
  void run(() => deviceStore.setEnabled(props.device.id, !props.device.enabled), 'device.actions.enableFailed')
}

async function onRelease(release: boolean) {
  try {
    await deviceStore.release(props.device.id, release)
    notifications.success(t(release ? 'bluetooth.release' : 'bluetooth.reclaim'))
  } catch {
    notifications.error(t('device.actions.enableFailed'))
  }
}

function onBtInfo() {
  btInfoOpen.value = true
}

function onClaim() {
  void run(() => claimAudio(props.device.id), 'device.actions.enableFailed')
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
  <SbCard :class="{ 'opacity-50': !device.enabled }">
    <template #header>
      <div class="flex min-w-0 flex-1 items-center gap-2">
        <input
          v-if="selectable"
          type="checkbox"
          :checked="selected"
          class="h-4 w-4 shrink-0 cursor-pointer rounded border-border-strong text-primary-text accent-primary focus:ring-primary"
          :aria-label="`Select ${device.name}`"
          @click.stop
          @change.stop="emit('toggleSelect', device.id)"
        />
        <Bluetooth class="h-4 w-4 shrink-0 text-text-secondary" />
        <span class="truncate font-semibold text-text-primary">
          {{ device.name }}
        </span>
        <span
          v-if="battery != null && BatteryIcon"
          class="ml-auto flex shrink-0 items-center gap-0.5 text-xs"
          :class="batteryColor"
          :title="t('device.battery', { level: battery })"
        >
          <component :is="BatteryIcon" class="h-3.5 w-3.5" />
          {{ battery }}%
        </span>
      </div>
    </template>

    <template #actions>
      <div class="flex items-center gap-2">
        <DeviceStatusBadge :state="state" />
        <SbDropdown align="right">
          <template #trigger>
            <button
              type="button"
              class="-m-2 inline-flex size-10 cursor-pointer items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
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
          <SbDropdownItem @click="onToggleEnabled">
            {{ device.enabled ? t('device.actions.disable') : t('device.actions.enable') }}
          </SbDropdownItem>
          <SbDropdownItem v-if="!released" @click="onRelease(true)">
            {{ t('bluetooth.release') }}
          </SbDropdownItem>
          <SbDropdownItem v-else @click="onRelease(false)">
            {{ t('bluetooth.reclaim') }}
          </SbDropdownItem>
          <SbDropdownItem v-if="connected" @click="onClaim">
            {{ t('device.actions.claim') }}
          </SbDropdownItem>
          <SbDropdownItem @click="onBtInfo">
            {{ t('bluetooth.info') }}
          </SbDropdownItem>
          <SbDropdownItem @click="onDetails">
            {{ t('device.actions.details') }}
          </SbDropdownItem>
          <SbDropdownItem :destructive="true" @click="onForget">
            {{ t('device.actions.forget') }}
          </SbDropdownItem>
        </SbDropdown>
      </div>
    </template>

    <!-- Body -->
    <div class="space-y-3">
      <!-- Backend type badge -->
      <div class="flex items-center gap-2">
        <SbBadge v-if="device.bluetooth.adapter.hci" tone="neutral" size="sm">
          {{ device.bluetooth.adapter.hci }}
        </SbBadge>
        <SbBadge v-if="released" tone="warning" size="sm">
          {{ t('bluetooth.released') }}
        </SbBadge>
        <span
          v-if="device.timing.reanchor_count"
          class="inline-flex items-center gap-0.5 text-xs text-text-secondary"
        >
          <Anchor class="h-3 w-3" />
          {{ t('device.reanchored', { count: device.timing.reanchor_count }) }}
        </span>
        <span class="text-xs text-text-secondary">{{ device.bluetooth.mac }}</span>
      </div>

      <!-- Volume slider -->
      <VolumeSlider
        v-if="connected"
        :mac="device.bluetooth.mac ?? device.id"
        :name="device.name"
        :volume="device.audio.volume"
        :muted="device.audio.muted"
        :disabled="!device.audio.has_sink"
        @update:volume="onVolumeUpdate"
        @update:muted="onMuteUpdate"
      />

      <!-- Transport controls -->
      <div
        v-if="connected && isStreaming && supports('next')"
        class="flex items-center justify-center gap-1"
      >
        <button
          type="button"
          class="rounded-full p-1.5 text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
          :aria-label="t('transport.previous')"
          :disabled="transportLoading"
          @click="onTransport('previous')"
        >
          <SkipBack class="h-4 w-4" />
        </button>
        <button
          type="button"
          class="rounded-full bg-primary/10 p-2 text-primary-text transition-colors hover:bg-primary/20"
          :aria-label="isStreaming ? t('transport.pause') : t('transport.play')"
          :disabled="transportLoading"
          @click="onTransport(isStreaming ? 'pause' : 'play')"
        >
          <Pause v-if="isStreaming" class="h-5 w-5" />
          <Play v-else class="h-5 w-5" />
        </button>
        <button
          type="button"
          class="rounded-full p-1.5 text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
          :aria-label="t('transport.next')"
          :disabled="transportLoading"
          @click="onTransport('next')"
        >
          <SkipForward class="h-4 w-4" />
        </button>
      </div>

      <!-- Playback progress -->
      <PlaybackProgress v-if="isStreaming" :device="device" />

      <!-- Now playing -->
      <div
        v-if="nowPlaying && isStreaming"
        class="flex items-center gap-2 rounded-lg bg-surface-secondary p-2"
      >
        <Music class="h-4 w-4 shrink-0 text-primary-text" />
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

    <!-- BT Device Info Modal -->
    <BtDeviceInfoModal
      :mac="device.bluetooth.mac ?? ''"
      :adapter="device.bluetooth.adapter.mac ?? ''"
      :open="btInfoOpen"
      @update:open="btInfoOpen = $event"
    />
  </SbCard>
</template>
