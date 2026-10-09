<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { SbDrawer, SbTabs, SbTimeline, SbSignalPath, SbBadge, SbButton, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import BtDeviceInfoModal from '@/components/bluetooth/BtDeviceInfoModal.vue'
import { setAdapterPower } from '@/api/bluetooth'
import { openPairingWindow, reconnectDevice, repairDevice, setPowerSave, unmuteSink } from '@/api/devices'
import { ApiError } from '@/api/client'
import DeviceSettingsPanel from './DeviceSettingsPanel.vue'
import DeviceTimingPanel from './DeviceTimingPanel.vue'
import type { Device } from '@/api/types'
import { Power, RotateCw, Info, MoreHorizontal } from 'lucide-vue-next'

const props = defineProps<{
  deviceId: string | null
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const bridge = useBridgeStore()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()

const activeTab = ref('status')
const adapterLoading = ref(false)
const managementLoading = ref(false)
const btInfoOpen = ref(false)

const device = computed<Device | undefined>(() => (props.deviceId ? bridge.deviceById(props.deviceId) : undefined))
const released = computed(() => device.value?.bluetooth.management_enabled === false)
const adapterId = computed(() => device.value?.bluetooth.adapter.hci || '')
const drawerTitle = computed(() => device.value?.name ?? '')
const events = computed(() => device.value?.recent_events ?? [])

const tabs = computed(() => [
  { id: 'status', label: t('drawer.tabs.status') },
  { id: 'settings', label: t('drawer.tabs.config') },
  { id: 'timing', label: t('drawer.tabs.timing') },
  { id: 'events', label: t('drawer.tabs.events'), badge: events.value.length || undefined },
  { id: 'signal', label: t('drawer.tabs.signal') },
])

const signalSegments = computed(() => {
  const d = device.value
  if (!d) return []
  const sinkStatus = d.audio.streaming
    ? ('active' as const)
    : d.bluetooth.connected
      ? ('inactive' as const)
      : ('error' as const)
  return [
    { id: 'ma', label: 'Music Assistant', status: bridge.maConnected ? ('active' as const) : ('inactive' as const) },
    {
      id: 'sendspin',
      label: 'Sendspin',
      status: d.playback.server_connected ? ('active' as const) : ('inactive' as const),
      sublabel: d.sendspin.listen_port ? `port ${d.sendspin.listen_port}` : undefined,
    },
    {
      id: 'subprocess',
      label: t('drawer.signal.subprocess'),
      status: d.playback.connected ? ('active' as const) : ('inactive' as const),
    },
    { id: 'sink', label: d.audio.sink_name ?? 'Audio Sink', status: sinkStatus },
    { id: 'speaker', label: d.name ?? '', status: sinkStatus },
  ]
})

const timelineEvents = computed(() =>
  events.value.map((e) => {
    const at = String(e.at ?? '')
    return {
      id: `${at}-${String(e.event_type ?? '')}`,
      timestamp: at ? new Date(at).toLocaleString() : '',
      title: String(e.event_type ?? ''),
      description: e.message ? String(e.message) : undefined,
      type: (e.level === 'error' ? 'error' : 'info') as 'info' | 'error',
    }
  }),
)

function onDrawerUpdate(...args: unknown[]) {
  emit('update:open', Boolean(args[0]))
}

/** Rarely needed recovery actions, kept in one menu (Music Assistant keeps toolbars minimal). */
const actionBusy = ref(false)
async function runAction(kind: 'reconnect' | 'repair' | 'pairing' | 'unmute' | 'release' | 'resume') {
  const d = device.value
  if (!d) return
  actionBusy.value = true
  try {
    if (kind === 'reconnect') await reconnectDevice(d.id)
    else if (kind === 'repair') await repairDevice(d.id)
    else if (kind === 'pairing') await openPairingWindow(d.id)
    else if (kind === 'unmute') await unmuteSink(d.id)
    else await setPowerSave(d.id, kind === 'release')
    notifications.success(t(`drawer.actions.${kind}Done`))
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  } finally {
    actionBusy.value = false
  }
}

async function onRelease(release: boolean) {
  const d = device.value
  if (!d) return
  managementLoading.value = true
  try {
    await deviceStore.release(d.id, release)
    notifications.success(t(release ? 'bluetooth.release' : 'bluetooth.reclaim'))
  } catch {
    notifications.error(t('device.actions.enableFailed'))
  } finally {
    managementLoading.value = false
  }
}

function onBtInfo() {
  btInfoOpen.value = true
}

async function powerCycle(states: boolean[]) {
  if (!adapterId.value) return
  adapterLoading.value = true
  const reboot = states.length > 1
  try {
    for (const on of states) await setAdapterPower(adapterId.value, on)
    await bridge.refreshAdapters()
    notifications.success(t(reboot ? 'adapter.rebooted' : 'adapter.powerToggled'))
  } catch {
    notifications.error(t(reboot ? 'adapter.rebootFailed' : 'adapter.powerFailed'))
  } finally {
    adapterLoading.value = false
  }
}

function onAdapterPower() {
  const adapter = bridge.adapters.find((a) => a.id === adapterId.value)
  void powerCycle([!(adapter?.powered ?? true)])
}

function onAdapterReboot() {
  void powerCycle([false, true])
}

</script>

<template>
  <SbDrawer
    :model-value="open"
    :title="drawerTitle"
    side="right"
    width="max-w-lg"
    @update:model-value="onDrawerUpdate"
  >
    <template v-if="device">
      <SbTabs v-model="activeTab" :tabs="tabs">
        <!-- Status tab -->
        <template #status>
          <div class="space-y-4 py-4">
            <div class="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.playerState') }}</span>
                <div class="mt-1">
                  <DeviceStatusBadge :state="deviceState(device)" />
                </div>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.adapter') }}</span>
                <div class="mt-1">
                  <SbBadge tone="neutral" size="sm">
                    {{ adapterId || '—' }}
                  </SbBadge>
                </div>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.audioSink') }}</span>
                <p class="mt-1 font-mono text-xs text-text-primary">
                  {{ device.audio.sink_name ?? '—' }}
                </p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.connected') }}</span>
                <p class="mt-1 text-text-primary">
                  {{ device.bluetooth.connected ? t('common.yes') : t('common.no') }}
                </p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.codec') }}</span>
                <p class="mt-1 text-text-primary">{{ device.bluetooth.codec_name ?? '—' }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.sampleRate') }}</span>
                <p class="mt-1 text-text-primary">{{ device.audio.format ?? '—' }}</p>
              </div>
            </div>
            <div v-if="device.last_error" class="rounded-lg bg-error/10 p-3 text-sm text-error">
              {{ device.last_error }}
            </div>

            <!-- Release/Reclaim + BT Info actions -->
            <div class="flex items-center gap-2 border-t border-border pt-3">
              <SbButton
                v-if="!released"
                variant="outline"
                size="sm"
                :loading="managementLoading"
                @click="onRelease(true)"
              >
                {{ t('bluetooth.release') }}
              </SbButton>
              <SbButton
                v-else
                variant="outline"
                size="sm"
                :loading="managementLoading"
                @click="onRelease(false)"
              >
                {{ t('bluetooth.reclaim') }}
              </SbButton>
              <SbButton variant="outline" size="sm" @click="onBtInfo">
                <template #icon-left>
                  <Info class="h-3.5 w-3.5" />
                </template>
                {{ t('bluetooth.info') }}
              </SbButton>
              <SbDropdown align="right" class="ml-auto">
                <template #trigger>
                  <SbButton variant="ghost" size="sm" icon :loading="actionBusy" :title="t('drawer.actions.more')">
                    <MoreHorizontal />
                  </SbButton>
                </template>
                <SbDropdownItem @click="runAction('reconnect')">{{ t('drawer.actions.reconnect') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('repair')">{{ t('drawer.actions.repair') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('pairing')">{{ t('drawer.actions.pairing') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('unmute')">{{ t('drawer.actions.unmute') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('release')">{{ t('drawer.actions.release') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('resume')">{{ t('drawer.actions.resume') }}</SbDropdownItem>
              </SbDropdown>
              <SbBadge v-if="released" tone="warning" size="sm">
                {{ t('bluetooth.released') }}
              </SbBadge>
            </div>
          </div>
        </template>

        <!-- Settings tab -->
        <template #settings>
          <div class="text-sm">
            <DeviceSettingsPanel :device="device" />

            <!-- Adapter management -->
            <div v-if="adapterId" class="border-t border-border pt-3">
              <h4 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {{ t('adapter.management') }}
              </h4>
              <div class="flex items-center gap-2">
                <SbButton
                  variant="outline"
                  size="sm"
                  :loading="adapterLoading"
                  @click="onAdapterPower"
                >
                  <template #icon-left>
                    <Power class="h-3.5 w-3.5" />
                  </template>
                  {{ t('adapter.togglePower') }}
                </SbButton>
                <SbButton
                  variant="outline"
                  size="sm"
                  :loading="adapterLoading"
                  @click="onAdapterReboot"
                >
                  <template #icon-left>
                    <RotateCw class="h-3.5 w-3.5" />
                  </template>
                  {{ t('adapter.reboot') }}
                </SbButton>
              </div>
            </div>
          </div>
        </template>

        <!-- Timing tab -->
        <template #timing>
          <DeviceTimingPanel :device="device" />
        </template>

        <!-- Events tab -->
        <template #events>
          <div class="py-4">
            <div v-if="timelineEvents.length === 0" class="py-8 text-center text-text-secondary">
              {{ t('drawer.events.empty') }}
            </div>
            <SbTimeline v-else :events="timelineEvents" :max-items="10" />
          </div>
        </template>

        <!-- Signal Path tab -->
        <template #signal>
          <div class="space-y-4 py-4">
            <SbSignalPath :segments="signalSegments" direction="vertical" />

            <!-- Audio routing details -->
            <div class="border-t border-border pt-4">
              <h4 class="mb-3 text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {{ t('diagnostics.audioRouting') }}
              </h4>
              <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                <dt class="text-text-secondary">{{ t('diagnostics.audioSink') }}</dt>
                <dd class="font-mono text-xs text-text-primary">{{ device.audio.sink_name ?? '—' }}</dd>
                <dt class="text-text-secondary">{{ t('diagnostics.codec') }}</dt>
                <dd class="text-text-primary">{{ device.bluetooth.codec_name ?? '—' }}</dd>
                <dt class="text-text-secondary">{{ t('diagnostics.sampleRate') }}</dt>
                <dd class="text-text-primary">{{ device.audio.format ?? '—' }}</dd>
                <dt class="text-text-secondary">{{ t('diagnostics.reanchorCount') }}</dt>
                <dd class="text-text-primary">{{ device.timing.reanchor_count > 0 ? `${device.timing.reanchor_count} corrections` : '—' }}</dd>
              </dl>
            </div>
          </div>
        </template>
      </SbTabs>
    </template>

    <!-- BT Device Info Modal -->
    <BtDeviceInfoModal
      v-if="device"
      :mac="device.bluetooth.mac ?? ''"
      :adapter="device.bluetooth.adapter.mac ?? ''"
      :open="btInfoOpen"
      @update:open="btInfoOpen = $event"
    />
  </SbDrawer>
</template>
