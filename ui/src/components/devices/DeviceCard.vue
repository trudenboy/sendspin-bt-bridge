<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { deviceState } from '@/stores/devices'
import SpeakerActionsMenu from './SpeakerActionsMenu.vue'
import { speakerName } from '@/utils/speakerName'
import { releaseKind } from '@/utils/deviceStatusText'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import DevicePlayerPanel from './DevicePlayerPanel.vue'
import { BatteryLow, BatteryMedium, BatteryFull } from 'lucide-vue-next'
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

const state = computed(() => deviceState(props.device))
const displayName = computed(() => speakerName(props.device.name, props.device.bluetooth?.mac ?? props.device.id))
const room = computed(() => props.device.room?.name || null)

/* The badge names a release; the meta line keeps it visible when another state wins (disabled). */
const releasedNote = computed(() => {
  const kind = releaseKind(props.device)
  if (!kind || state.value === 'released') return null
  return kind === 'auto' ? t('device.status.autoReleased') : t('bluetooth.released')
})
const battery = computed(() => props.device.bluetooth?.battery_level ?? null)

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

function onDetails() {
  emit('openDetail', props.device.id)
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
          <span v-if="releasedNote" class="text-warning-text">{{ releasedNote }}</span>
        </p>
      </div>
      <DeviceStatusBadge :state="state" :device="device" class="relative z-10 shrink-0" />
      <div class="relative z-10 -mt-1.5 -mr-2"><SpeakerActionsMenu :device="device" /></div>
    </header>

    <DevicePlayerPanel :device="device" />
  </article>
</template>
