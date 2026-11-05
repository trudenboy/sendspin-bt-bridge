<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbButton } from '@/kit'
import DeviceCardShell from '@/components/devices/card/DeviceCardShell.vue'
import HardwareCardMenu from './HardwareCardMenu.vue'
import { hardwareCardView } from '@/components/devices/card/viewModels'
import type { BtDeviceSummary } from '@/api/types'

const props = withDefaults(
  defineProps<{
    device: BtDeviceSummary
    adding?: boolean
    forgetting?: boolean
    connecting?: boolean
    disconnecting?: boolean
    refusal?: { playerId: string | null; remediation: string | null; detail: string | null } | null
    sourceDevice?: boolean
  }>(),
  { adding: false, forgetting: false, connecting: false, disconnecting: false, refusal: null, sourceDevice: false },
)

const emit = defineEmits<{
  open: [mac: string]
  add: [device: BtDeviceSummary]
  forget: [device: BtDeviceSummary]
  connect: [device: BtDeviceSummary]
  disconnect: [device: BtDeviceSummary]
}>()

const { t } = useI18n()
const view = computed(() => hardwareCardView(props.device, props.sourceDevice))
const canAdd = computed(() => !props.device.player_id && !props.sourceDevice)
const rssi = computed(() => {
  const value = props.device.rssi?.delta_db
  if (value == null) return null
  return value > 0 ? `+${value}` : String(value)
})
const playerHref = computed(() =>
  props.refusal?.playerId ? `/?player=${encodeURIComponent(props.refusal.playerId)}` : '/',
)

function action(id: string) {
  if (id === 'connect') emit('connect', props.device)
  else if (id === 'open') emit('open', props.device.mac)
}
</script>

<template>
  <DeviceCardShell :view="view" @open="emit('open', $event)" @action="action">
    <template #menu>
      <HardwareCardMenu
        :device="device"
        :source-device="sourceDevice"
        :can-add="canAdd"
        @add="emit('add', $event)"
        @forget="emit('forget', $event)"
        @disconnect="emit('disconnect', $event)"
      />
    </template>

    <template #badges>
      <SbBadge v-if="device.battery_pct !== null" size="sm">{{ t('hardware.battery', { pct: device.battery_pct }) }}</SbBadge>
      <SbBadge v-if="rssi" size="sm">{{ t('hardware.signal', { db: rssi }) }}</SbBadge>
      <SbBadge v-if="sourceDevice" data-source-device tone="info" size="sm">{{ t('hardware.sourceDevice') }}</SbBadge>
    </template>

    <template #context>
      <p class="text-sm text-text-secondary">{{ t(view.contextKey ?? 'hardware.context.bond') }}</p>
    </template>

    <template #footer>
      <SbButton v-if="view.primary?.id === 'connect'" size="sm" variant="outline" data-connect-device :loading="connecting" @click="emit('connect', device)">
        {{ t('hardware.connect') }}
      </SbButton>
      <SbButton v-else-if="view.primary?.id === 'open'" size="sm" variant="outline" data-device-details @click="emit('open', device.mac)">
        {{ t('hardware.openDetailsShort') }}
      </SbButton>
    </template>

    <template #extra>
      <p v-if="device.player_id" class="relative z-10 text-sm">
        <span class="text-text-secondary">{{ t('hardware.player') }}:</span>
        <RouterLink data-player-link to="/" class="ml-1 text-primary-text underline-offset-2 hover:underline">
          {{ device.player_id }}
        </RouterLink>
      </p>

      <div
        v-if="refusal"
        data-bond-refusal
        role="alert"
        class="relative z-10 rounded-(--radius-card) border border-warning/40 bg-warning/10 p-3 text-sm"
      >
        <p class="font-medium text-text-primary">{{ t('hardware.bondInUseTitle') }}</p>
        <p class="mt-1 text-text-secondary">{{ refusal.remediation || refusal.detail || t('hardware.bondInUse') }}</p>
        <RouterLink
          v-if="refusal.playerId"
          :to="playerHref"
          data-bond-player-link
          class="mt-1 inline-block font-medium text-primary-text underline-offset-2 hover:underline"
        >
          {{ t('hardware.openPlayer', { player: refusal.playerId }) }}
        </RouterLink>
      </div>
    </template>
  </DeviceCardShell>
</template>
