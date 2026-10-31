<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Power } from 'lucide-vue-next'
import { SbBadge, SbButton, SbDrawer } from '@/kit'
import AudioEntityBody from './AudioEntityBody.vue'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import type { BtDeviceSummary, ControllerSummary } from '@/api/types'

type DrawerKind = 'controller' | 'device' | 'audio-card' | 'audio-sink' | 'audio-source'

const props = withDefaults(
  defineProps<{
    open: boolean
    kind: DrawerKind
    controller?: ControllerSummary | null
    device?: BtDeviceSummary | null
    /** Audio entities are addressed by their stable name, not by a synthetic id. */
    audioName?: string | null
    /** What the page already knows, so the drawer title is right before the read lands. */
    audioLabel?: string | null
    busy?: boolean
    connecting?: boolean
    disconnecting?: boolean
  }>(),
  { controller: null, device: null, audioName: null, audioLabel: null, busy: false, connecting: false, disconnecting: false },
)

const emit = defineEmits<{
  'update:open': [value: boolean]
  togglePower: [controller: ControllerSummary]
  connect: [device: BtDeviceSummary]
  disconnect: [device: BtDeviceSummary]
}>()

const { t } = useI18n()

const controller = computed(() => (props.kind === 'controller' ? props.controller : null))
const device = computed(() => (props.kind === 'device' ? props.device : null))
/** Same rule as the card: a bond that is down can be brought up; a live link can be dropped. */
const canConnect = computed(
  () => device.value !== null && !device.value.connected && (device.value.paired === true || device.value.bonded === true),
)
const audioKind = computed<'audio-card' | 'audio-sink' | 'audio-source' | null>(() =>
  props.kind === 'audio-card' || props.kind === 'audio-sink' || props.kind === 'audio-source' ? props.kind : null,
)
const name = computed(() => {
  if (audioKind.value) return props.audioLabel || props.audioName || ''
  const entry = controller.value ?? device.value
  return entry ? entry.alias || entry.name || entry.mac : ''
})
const mac = computed(() => controller.value?.mac ?? device.value?.mac ?? '')
const area = computed(() => controller.value?.area?.name || controller.value?.area?.id || null)
const rssi = computed(() => {
  const value = device.value?.rssi?.delta_db
  if (value == null) return null
  return value > 0 ? `+${value}` : String(value)
})
</script>

<template>
  <SbDrawer
    :model-value="open"
    :title="name"
    width="max-w-lg"
    @update:model-value="(value: unknown) => emit('update:open', Boolean(value))"
  >
    <div class="space-y-6 py-2">
      <p v-if="mac" class="font-mono text-xs text-text-secondary">{{ mac }}</p>

      <AudioEntityBody v-if="audioKind && audioName" :kind="audioKind" :name="audioName" />

      <section v-if="controller" data-drawer-overview class="space-y-3">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.drawer.overview') }}
        </h3>
        <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-text-secondary">{{ t('hardware.drawer.power') }}</dt>
          <dd>
            <SbBadge :tone="controller.powered ? 'success' : 'neutral'" size="sm">
              {{ controller.powered ? t('hardware.powered') : t('hardware.poweredOff') }}
            </SbBadge>
          </dd>
          <dt class="text-text-secondary">{{ t('hardware.drawer.present') }}</dt>
          <dd class="text-text-primary">{{ controller.present ? t('hardware.yes') : t('hardware.no') }}</dd>
          <template v-if="area">
            <dt class="text-text-secondary">{{ t('hardware.drawer.area') }}</dt>
            <dd class="text-text-primary">{{ area }}</dd>
          </template>
          <template v-if="controller.class_of_device">
            <dt class="text-text-secondary">{{ t('hardware.drawer.classOfDevice') }}</dt>
            <dd class="font-mono text-xs text-text-primary">{{ controller.class_of_device }}</dd>
          </template>
          <dt class="text-text-secondary">{{ t('hardware.drawer.deviceCount') }}</dt>
          <dd class="text-text-primary">{{ controller.device_count }}</dd>
        </dl>
      </section>

      <section v-if="device" data-drawer-overview class="space-y-3">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.drawer.overview') }}
        </h3>
        <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-text-secondary">{{ t('hardware.connected') }}</dt>
          <dd class="text-text-primary">{{ device.connected ? t('hardware.yes') : t('hardware.no') }}</dd>
          <dt class="text-text-secondary">{{ t('hardware.paired') }}</dt>
          <dd class="text-text-primary">{{ device.paired === true ? t('hardware.yes') : device.paired === false ? t('hardware.no') : t('hardware.unknown') }}</dd>
          <dt class="text-text-secondary">{{ t('hardware.trusted') }}</dt>
          <dd class="text-text-primary">{{ device.trusted === true ? t('hardware.yes') : device.trusted === false ? t('hardware.no') : t('hardware.unknown') }}</dd>
          <dt class="text-text-secondary">{{ t('hardware.drawer.bonded') }}</dt>
          <dd class="text-text-primary">{{ device.bonded === true ? t('hardware.yes') : device.bonded === false ? t('hardware.no') : t('hardware.unknown') }}</dd>
          <template v-if="device.battery_pct !== null">
            <dt class="text-text-secondary">{{ t('hardware.drawer.battery') }}</dt>
            <dd class="text-text-primary">{{ device.battery_pct }}%</dd>
          </template>
          <template v-if="rssi">
            <dt class="text-text-secondary">{{ t('hardware.drawer.rssi') }}</dt>
            <dd class="text-text-primary">{{ t('hardware.rssi', { db: rssi }) }}</dd>
          </template>
          <template v-if="device.player_id">
            <dt class="text-text-secondary">{{ t('hardware.drawer.player') }}</dt>
            <dd>
              <RouterLink to="/" class="text-primary-text underline-offset-2 hover:underline">{{ device.player_id }}</RouterLink>
            </dd>
          </template>
        </dl>

        <div v-if="device.roles.length" class="space-y-1.5">
          <p class="text-text-secondary">{{ t('hardware.drawer.roles') }}</p>
          <div class="flex flex-wrap gap-1.5">
            <SbBadge
              v-for="role in device.roles"
              :key="`${role.role}-${role.evidence}`"
              size="sm"
              :tone="role.role === 'input' ? 'info' : 'success'"
              :data-role="role.role"
            >
              {{ role.role === 'input' ? t('hardware.roleInput') : t('hardware.roleOutput') }}
              <span class="font-normal opacity-80">· {{ role.evidence }}</span>
            </SbBadge>
          </div>
        </div>
      </section>

      <section v-if="technicalDetails" data-drawer-technical class="border-t border-border pt-4">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.drawer.technical') }}
        </h3>
        <dl v-if="controller" class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-text-secondary">{{ t('hardware.drawer.id') }}</dt>
          <dd class="font-mono text-xs text-text-primary">{{ controller.id }}</dd>
          <dt class="text-text-secondary">{{ t('hardware.drawer.mac') }}</dt>
          <dd class="font-mono text-xs text-primary-text break-all">{{ controller.mac }}</dd>
          <template v-if="controller.id_synthetic">
            <dt class="text-text-secondary">{{ t('hardware.drawer.synthetic') }}</dt>
            <dd class="text-text-primary">{{ t('hardware.yes') }}</dd>
          </template>
        </dl>
        <dl v-else-if="device" class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-text-secondary">{{ t('hardware.drawer.mac') }}</dt>
          <dd class="font-mono text-xs text-primary-text break-all">{{ device.mac }}</dd>
          <dt class="text-text-secondary">{{ t('hardware.drawer.controllers') }}</dt>
          <dd class="font-mono text-xs text-text-primary">{{ device.controller_macs.join(', ') || '—' }}</dd>
          <template v-if="device.rssi?.at">
            <dt class="text-text-secondary">{{ t('hardware.drawer.observedAt') }}</dt>
            <dd class="text-text-primary">{{ device.rssi.at }}</dd>
          </template>
        </dl>
      </section>
    </div>

    <template v-if="controller" #footer>
      <SbButton
        variant="outline"
        size="sm"
        data-drawer-power
        :loading="busy"
        @click="emit('togglePower', controller)"
      >
        <template #icon-left><Power class="h-3.5 w-3.5" aria-hidden="true" /></template>
        {{ controller.powered ? t('adapter.turnOff') : t('adapter.turnOn') }}
      </SbButton>
    </template>

    <!-- A bond is a link, not a player: the drawer offers the same two commands as the card. -->
    <template v-else-if="device" #footer>
      <SbButton
        v-if="canConnect"
        variant="outline"
        size="sm"
        data-drawer-connect
        :loading="connecting"
        @click="emit('connect', device)"
      >
        {{ t('hardware.connect') }}
      </SbButton>
      <SbButton
        v-if="device.connected"
        variant="outline"
        size="sm"
        data-drawer-disconnect
        :loading="disconnecting"
        @click="emit('disconnect', device)"
      >
        {{ t('hardware.disconnect') }}
      </SbButton>
    </template>
  </SbDrawer>
</template>
