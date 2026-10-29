<script setup lang="ts">
import { speakerName } from '@/utils/speakerName'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { isDeviceConnected } from '@/utils/deviceBackend'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import PlaybackProgress from '@/components/playback/PlaybackProgress.vue'
import {
  Bluetooth,
  ChevronRight,
  CircleAlert,
  Speaker,
  Pause,
  SkipBack,
  SkipForward,
  BatteryLow,
  BatteryMedium,
  BatteryFull,
} from 'lucide-vue-next'
import SpeakerActionsMenu from './SpeakerActionsMenu.vue'
import SyncChip from './chips/SyncChip.vue'
import SignalChip from './chips/SignalChip.vue'
import GroupChip from './chips/GroupChip.vue'
import ServerChip from './chips/ServerChip.vue'
import LinkChip from './chips/LinkChip.vue'
import DevicePlayerPanel from './DevicePlayerPanel.vue'
import { canDo } from '@/utils/supportedActions'
import { blockedHints } from '@/utils/deviceChips'
import { isPulseSink } from '@/utils/deviceBackend'
import { transport } from '@/api/playback'
import { SbButton } from '@/kit'
import type { Device } from '@/api/types'

const props = withDefaults(
  defineProps<{
    device: Device
    selectable?: boolean
    selected?: boolean
    /** Show the card-equivalent block under the row. */
    expanded?: boolean
  }>(),
  {
    selectable: false,
    selected: false,
    expanded: false,
  },
)

const emit = defineEmits<{
  openDetail: [id: string]
  toggleSelect: [id: string]
  toggleExpand: [id: string]
}>()

const { t } = useI18n()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()

const state = computed(() => deviceState(props.device))
const connected = computed(() => isDeviceConnected(props.device))
const isStreaming = computed(() => props.device.playback.playing && props.device.audio.streaming)
const battery = computed(() => props.device.bluetooth?.battery_level ?? null)
const isOutput = computed(() => isPulseSink(props.device))

/** Unavailable controls, compact: one flag whose tooltip carries each reason. */
const blockedTitle = computed(() =>
  blockedHints(props.device)
    .map((h) => {
      const reason = h.key === 'sink_muted' ? t('device.chips.blocked.sinkMutedReason') : h.message
      return `${t(`device.chips.blocked.title.${h.key}`)}: ${reason}`
    })
    .join('\n'),
)

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
  if (level <= 50) return 'text-warning-text'
  return 'text-success'
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

const displayName = computed(() => speakerName(props.device.name, props.device.bluetooth?.mac ?? props.device.id))
const detailId = computed(() => `device-row-detail-${props.device.id}`)
/* The expanded row spans every column: checkbox, name, status, volume, transport, adapter, actions. */
const columns = computed(() => (props.selectable ? 7 : 6))

/** A click on the row's own surface (not one of its controls) opens or closes it. */
const INTERACTIVE = 'button, a, input, select, textarea, label, summary, [role="slider"], [role="menu"], [role="menuitem"]'
function onRowClick(e: MouseEvent) {
  if (!props.device.enabled) return
  const target = e.target as HTMLElement | null
  if (target?.closest(INTERACTIVE)) return
  emit('toggleExpand', props.device.id)
}

/* Quick actions in the expanded row (stable's list detail): enabled Bluetooth speakers only. */
const isBluetooth = computed(() => !isOutput.value && props.device.bluetooth != null && props.device.enabled)
const handedOver = computed(() => props.device.bluetooth?.management_enabled === false)
const standby = computed(() => props.device.bluetooth?.standby === true)

function quick(action: () => Promise<unknown>) {
  void run(action, 'common.error')
}

function onTransport(command: 'play' | 'pause' | 'previous' | 'next') {
  void run(() => transport(props.device.id, command), 'transport.failed')
}


</script>

<template>
  <tr
    class="border-b border-border transition-colors hover:bg-surface-secondary"
    :class="{ 'opacity-50': !device.enabled, 'cursor-pointer': device.enabled, 'border-b-0 bg-surface-secondary/60': expanded }"
    @click="onRowClick"
  >
    <!-- Checkbox -->
    <td v-if="selectable" class="w-10 py-2 pl-3 pr-1">
      <input
        type="checkbox"
        :checked="selected"
        class="h-4 w-4 cursor-pointer rounded border-border-strong text-primary-text accent-primary focus:ring-primary"
        :aria-label="t('device.select', { name: speakerName(device.name) })"
        @click.stop
        @change.stop="emit('toggleSelect', device.id)"
      />
    </td>

    <!-- Name + icon -->
    <td class="py-2 pl-3 pr-2">
      <div class="flex items-center gap-2">
        <button
          v-if="device.enabled"
          type="button"
          data-row-toggle
          class="-ml-1 inline-flex size-6 shrink-0 items-center justify-center rounded text-text-secondary hover:bg-surface-card hover:text-text-primary"
          :aria-expanded="expanded ? 'true' : 'false'"
          :aria-controls="expanded ? detailId : undefined"
          :aria-label="expanded ? t('devices.list.collapse', { name: displayName }) : t('devices.list.expand', { name: displayName })"
          @click="emit('toggleExpand', device.id)"
        >
          <ChevronRight class="size-4 transition-transform" :class="{ 'rotate-90': expanded }" aria-hidden="true" />
        </button>
        <span
          data-backend-icon
          class="inline-flex shrink-0 text-text-secondary"
          :title="isOutput ? t('device.chips.backend.output') : t('device.chips.backend.bluetooth')"
        >
          <Speaker v-if="isOutput" class="h-4 w-4" aria-hidden="true" />
          <Bluetooth v-else class="h-4 w-4" aria-hidden="true" />
          <span class="sr-only">{{ isOutput ? t('device.chips.backend.output') : t('device.chips.backend.bluetooth') }}</span>
        </span>
        <button
          type="button"
          class="max-w-80 truncate text-sm font-medium text-text-primary hover:text-primary-text"
          :title="device.name ?? undefined"
          @click="emit('openDetail', device.id)"
        >
          {{ speakerName(device.name, device.bluetooth?.mac ?? device.id) }}
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
      <div class="mt-1 flex flex-wrap items-center gap-1.5 pl-6 empty:hidden">
        <GroupChip :device="device" />
        <ServerChip :device="device" />
        <SignalChip :device="device" />
      </div>
    </td>

    <!-- Status -->
    <td class="whitespace-nowrap px-2 py-2">
      <div class="flex flex-wrap items-center gap-1.5">
        <DeviceStatusBadge :state="state" :device="device" />
        <SyncChip :device="device" />
        <span v-if="blockedTitle" data-blocked-flag class="inline-flex text-warning-text" :title="blockedTitle">
          <CircleAlert class="h-4 w-4" aria-hidden="true" />
          <span class="sr-only">{{ t('device.chips.blocked.summary') }}: {{ blockedTitle }}</span>
        </span>
      </div>
    </td>

    <!-- Volume + mute -->
    <td class="w-48 px-2 py-2">
      <VolumeSlider
        v-if="connected"
        class="min-w-40"
        :mac="device.bluetooth?.mac ?? device.id"
        :name="device.name"
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
            class="rounded p-1 text-primary-text hover:text-primary/80"
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
      <div class="max-w-48">
        <LinkChip :device="device" />
      </div>
    </td>

    <!-- Actions -->
    <td class="py-2 pl-2 pr-3 text-right">
      <SpeakerActionsMenu :device="device" />
    </td>
  </tr>
  <tr
    v-if="expanded"
    :id="detailId"
    data-row-detail
    class="border-b border-border bg-surface-secondary/60"
  >
    <td :colspan="columns" class="px-3 pb-3 pt-1">
      <div class="flex max-w-3xl flex-col gap-3">
        <DevicePlayerPanel :device="device" :volume="false" />
        <div
          v-if="isBluetooth"
          class="flex flex-wrap items-center gap-2"
          role="group"
          :aria-label="t('devices.list.quickActions')"
        >
          <template v-if="handedOver">
            <SbButton size="sm" variant="secondary" data-quick="reclaim" @click="quick(() => deviceStore.release(device.id, false))">
              {{ t('speaker.takeBack') }}
            </SbButton>
          </template>
          <template v-else>
            <SbButton v-if="canDo(device, 'reconnect')" size="sm" variant="secondary" data-quick="reconnect" @click="quick(() => deviceStore.reconnect(device.id))">
              {{ t('speaker.reconnect') }}
            </SbButton>
            <template v-if="canDo(device, 'standby')">
              <SbButton v-if="standby" size="sm" variant="secondary" data-quick="wake" @click="quick(() => deviceStore.wake(device.id))">
                {{ t('speaker.wake') }}
              </SbButton>
              <SbButton v-else size="sm" variant="secondary" data-quick="standby" @click="quick(() => deviceStore.standby(device.id))">
                {{ t('speaker.standby') }}
              </SbButton>
            </template>
            <SbButton size="sm" variant="secondary" data-quick="release" @click="quick(() => deviceStore.release(device.id, true))">
              {{ t('speaker.handOver') }}
            </SbButton>
          </template>
        </div>
      </div>
    </td>
  </tr>
</template>
