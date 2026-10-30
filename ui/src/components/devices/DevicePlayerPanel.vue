<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDeviceStore } from '@/stores/devices'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { isDeviceConnected } from '@/utils/deviceBackend'
import { speakerName } from '@/utils/speakerName'
import { actionFailure, type ActionKind } from '@/utils/actionFailure'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import PlaybackProgress from '@/components/playback/PlaybackProgress.vue'
import ArtworkThumb from '@/components/playback/ArtworkThumb.vue'
import SyncChip from './chips/SyncChip.vue'
import SignalChip from './chips/SignalChip.vue'
import GroupChip from './chips/GroupChip.vue'
import LinkChip from './chips/LinkChip.vue'
import ServerChip from './chips/ServerChip.vue'
import StatusState from '@/components/StatusState.vue'
import BlockedHints from './chips/BlockedHints.vue'
import { SbBadge } from '@/kit'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { transferChip } from '@/utils/deviceChips'
import { apiUrl } from '@/api/client'
import { Play, Pause, SkipBack, SkipForward, Shuffle, Repeat, Repeat1 } from 'lucide-vue-next'
import { queueControls, sendQueueCommand } from '@/utils/queueControls'
import { transport } from '@/api/playback'
import type { Device } from '@/api/types'
import type { MaNowPlaying } from '@/composables/usePlaybackProgress'

/**
 * A speaker's player block, shared by the device card and the expanded list
 * row: where it plays through and its chips, now playing with artwork and
 * transport, progress, volume, and the controls it cannot use right now.
 * Each part is a direct child (a fragment), so the parent's layout spaces them.
 */
const props = withDefaults(defineProps<{ device: Device; volume?: boolean }>(), { volume: true })

const { t, te } = useI18n()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()

const transportLoading = ref(false)
const displayName = computed(() => speakerName(props.device.name, props.device.bluetooth?.mac ?? props.device.id))
const connected = computed(() => isDeviceConnected(props.device))
const isStreaming = computed(() => props.device.playback.playing && props.device.audio.streaming)

/** Title/artist: the daemon's track metadata, else Music Assistant's now-playing. */
const nowPlaying = computed(() => {
  const track = props.device.playback.track
  if (track.title) return { title: track.title, artist: track.artist ?? null }
  const np = props.device.music_assistant.now_playing as MaNowPlaying | null
  if (np?.track) return { title: String(np.track), artist: np.artist ? String(np.artist) : null }
  return null
})

/** Music Assistant's artwork, through the bridge's signed same-origin proxy. */
const artworkUrl = computed(() => {
  const np = props.device.music_assistant.now_playing as MaNowPlaying | null
  const path = typeof np?.image_url === 'string' ? np.image_url : ''
  return path ? apiUrl(`/${path.replace(/^\//, '')}`) : null
})

const transfer = computed(() => transferChip(props.device))
const transferTitle = computed(() => {
  const chip = transfer.value
  if (!chip) return ''
  if (chip.ready) return t('device.chips.transfer.readyTitle')
  const key = `device.chips.transfer.reasons.${chip.reason}`
  return t('device.chips.transfer.notReadyTitle', { reason: te(key) ? t(key) : chip.reason.replace(/_/g, ' ') })
})

const supports = (command: string) => {
  const list = props.device.playback.supported_commands ?? []
  return list.length === 0 || list.includes(command)
}

async function run(action: () => Promise<unknown>, kind: ActionKind) {
  try {
    await action()
  } catch (e) {
    notifications.error(actionFailure(t, kind, speakerName(props.device.name), e))
  }
}

function onVolumeUpdate(level: number) {
  void run(() => deviceStore.setVolume(props.device.id, level), 'volume')
}

function onMuteUpdate(muted: boolean) {
  void run(() => deviceStore.setMute(props.device.id, muted), 'mute')
}

/* Shuffle, repeat and seek: one command at a time, none while Music Assistant confirms one. */
const bridge = useBridgeStore()
const queue = computed(() => queueControls(props.device))
const queueBusy = ref(false)
const queueLocked = computed(() => queueBusy.value || queue.value.pending)
const showQueueRow = computed(() => connected.value && (isStreaming.value || (!!nowPlaying.value && queue.value.any)))
const shuffleLabel = computed(() => t(queue.value.shuffle.on ? 'transport.shuffleOn' : 'transport.shuffleOff'))
const repeatLabel = computed(() => t(`transport.repeat.${queue.value.repeat.mode}`))

/** Native commands echo back with the next status; show the change now, as stable does. */
function predict(kind: 'shuffle' | 'repeat', before: ReturnType<typeof queueControls>) {
  const live = bridge.deviceById(props.device.id)
  if (!live) return
  const np = live.music_assistant.now_playing as Record<string, unknown> | null
  if (kind === 'shuffle' && before.shuffle.native) {
    live.playback.shuffle = !before.shuffle.on
    if (np) np.shuffle = !before.shuffle.on
  } else if (kind === 'repeat' && before.repeat.native) {
    const next = ({ off: 'all', all: 'one', one: 'off' } as const)[before.repeat.mode]
    live.playback.repeat_mode = next
    if (np) np.repeat = next
  }
}

async function onQueue(kind: 'shuffle' | 'repeat' | 'seek', positionMs?: number) {
  if (queueLocked.value) return
  const before = queue.value
  queueBusy.value = true
  try {
    await sendQueueCommand(props.device, kind, positionMs)
    if (kind !== 'seek') predict(kind, before)
  } catch {
    notifications.error(t('transport.failed'))
  } finally {
    queueBusy.value = false
  }
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
  <!-- Where it plays through, its group, sync and signal -->
  <div class="relative z-10 flex min-w-0 flex-wrap items-center gap-1.5 empty:hidden">
    <LinkChip :device="device" />
    <StatusState kind="bluetooth" :status="device.links.bluetooth" only-if-not-ok />
    <StatusState kind="audio_link" :status="device.links.audio_link" only-if-not-ok />
    <GroupChip :device="device" />
    <ServerChip :device="device" />
    <SyncChip :device="device" />
    <SignalChip :device="device" />
    <span v-if="technicalDetails && transfer" data-chip="transfer" class="inline-flex" :title="transferTitle">
      <SbBadge :tone="transfer.tone" size="sm">
        {{ transfer.ready ? t('device.chips.transfer.ready') : t('device.chips.transfer.notReady') }}
      </SbBadge>
    </span>
  </div>

  <!-- Now playing with its main control -->
  <div v-if="nowPlaying && connected" class="relative z-10 flex items-center gap-3 rounded-(--radius-button) bg-surface-secondary px-3 py-2">
    <ArtworkThumb :url="artworkUrl" :alt="nowPlaying.title" size-class="size-10" icon-class="size-4" />
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
  <!-- Shuffle, the (seekable) progress, repeat -->
  <div
    v-if="showQueueRow"
    class="relative z-10 flex items-center gap-2 empty:hidden"
    role="group"
    :aria-label="t('transport.queue')"
  >
    <button
      v-if="queue.shuffle.available"
      type="button"
      data-queue="shuffle"
      class="inline-flex size-8 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-surface-secondary disabled:cursor-wait disabled:opacity-50"
      :class="queue.shuffle.on ? 'text-primary-text' : 'text-text-secondary hover:text-text-primary'"
      :aria-pressed="queue.shuffle.on ? 'true' : 'false'"
      :aria-label="shuffleLabel"
      :title="queue.pending ? t('transport.pending') : shuffleLabel"
      :disabled="queueLocked"
      @click="onQueue('shuffle')"
    >
      <Shuffle class="size-4" aria-hidden="true" />
    </button>
    <PlaybackProgress
      v-if="isStreaming"
      :device="device"
      class="min-w-0 flex-1"
      :seekable="queue.seek.available"
      :disabled="queueLocked"
      @seek="onQueue('seek', $event)"
    />
    <span v-else class="flex-1" />
    <button
      v-if="queue.repeat.available"
      type="button"
      data-queue="repeat"
      class="inline-flex size-8 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-surface-secondary disabled:cursor-wait disabled:opacity-50"
      :class="queue.repeat.mode !== 'off' ? 'text-primary-text' : 'text-text-secondary hover:text-text-primary'"
      :aria-pressed="queue.repeat.mode !== 'off' ? 'true' : 'false'"
      :aria-label="repeatLabel"
      :title="queue.pending ? t('transport.pending') : repeatLabel"
      :disabled="queueLocked"
      @click="onQueue('repeat')"
    >
      <Repeat1 v-if="queue.repeat.mode === 'one'" class="size-4" aria-hidden="true" />
      <Repeat v-else class="size-4" aria-hidden="true" />
    </button>
  </div>

  <VolumeSlider
    v-if="volume && connected"
    class="relative z-10"
    :mac="device.bluetooth?.mac ?? device.id"
    :name="displayName"
    :volume="device.audio.volume"
    :muted="device.audio.muted"
    :disabled="!device.audio.has_sink"
    @update:volume="onVolumeUpdate"
    @update:muted="onMuteUpdate"
  />

  <BlockedHints :device="device" class="relative z-10" />
</template>
