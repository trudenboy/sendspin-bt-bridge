<script setup lang="ts">
import { speakerName } from '@/utils/speakerName'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { isDeviceConnected, isPulseSink } from '@/utils/deviceBackend'
import { SbDrawer, SbTabs, SbTimeline, SbSignalPath, SbBadge, SbButton, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import BtDeviceInfoModal from '@/components/bluetooth/BtDeviceInfoModal.vue'
import { setAdapterPower } from '@/api/bluetooth'
import { openPairingWindow, reconnectDevice, repairDevice, setPowerSave, unmuteSink } from '@/api/devices'
import { ApiError } from '@/api/client'
import DeviceSettingsPanel from './DeviceSettingsPanel.vue'
import DeviceTimingPanel from './DeviceTimingPanel.vue'
import type { Device } from '@/api/types'
import { canDo } from '@/utils/supportedActions'
import { releaseKind } from '@/utils/deviceStatusText'
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
const pulse = computed(() => (device.value ? isPulseSink(device.value) : false))
const released = computed(() => device.value?.bluetooth?.management_enabled === false)
const adapterId = computed(() => device.value?.bluetooth?.adapter.hci || '')
/* A disabled player has no Device: its details are its settings. */
const disabledPlayer = computed(() =>
  !device.value && props.deviceId ? deviceStore.disabledPlayers.find((p) => p.id === props.deviceId) : undefined,
)
const disabledTarget = computed(() => {
  const p = disabledPlayer.value
  return p ? { id: p.id, config_id: p.id, name: p.name, enabled: false, bluetooth: p.mac ? { mac: p.mac } : null } : null
})
const drawerTitle = computed(() =>
  disabledPlayer.value
    ? speakerName(disabledPlayer.value.name, disabledPlayer.value.mac ?? disabledPlayer.value.id)
    : speakerName(device.value?.name, device.value?.bluetooth?.mac ?? ''),
)
const events = computed(() => device.value?.recent_events ?? [])

// A speaker that disappears (removed) takes its panel with it.
watch(() => device.value ?? disabledPlayer.value, (now, before) => {
  if (before && !now && props.open) emit('update:open', false)
})

const tabs = computed(() => [
  { id: 'status', label: t('drawer.tabs.status') },
  { id: 'settings', label: t('drawer.tabs.config') },
  { id: 'timing', label: t('drawer.tabs.timing') },
  { id: 'events', label: t('drawer.tabs.events'), badge: events.value.length || undefined },
  { id: 'signal', label: t('drawer.tabs.signal') },
])

/* Which Music Assistant the player is on, in full; the card's chip is the short form. */
const serverInfo = computed(() => {
  const playback = device.value?.playback
  const s = playback?.server
  if (!playback?.server_connected || !s || !(s.name || s.host || s.id)) return null
  const rows = [
    { key: 'name', value: s.name },
    { key: 'host', value: s.host },
    { key: 'id', value: s.id, mono: true },
    { key: 'url', value: s.url, mono: true },
  ].filter((r): r is { key: string; value: string; mono?: boolean } => !!r.value)
  const where = s.name && s.host ? `${s.host} (${s.name})` : (s.name ?? s.host ?? '')
  return { rows, foreign: s.own === false, where }
})
const refusedServers = computed(() => device.value?.playback.refused_servers ?? [])

const signalSegments = computed(() => {
  const d = device.value
  if (!d) return []
  const sinkStatus = d.audio.streaming
    ? ('active' as const)
    : isDeviceConnected(d)
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
                  <DeviceStatusBadge :state="deviceState(device)" :device="device" />
                </div>
              </div>
              <div v-if="!pulse">
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
                  {{ isDeviceConnected(device) ? t('common.yes') : t('common.no') }}
                </p>
              </div>
              <div v-if="!pulse">
                <span class="text-text-secondary">{{ t('drawer.status.codec') }}</span>
                <p class="mt-1 text-text-primary">{{ device.bluetooth?.codec_name ?? '—' }}</p>
              </div>
              <div v-if="!pulse && device.bluetooth?.reported_delay_ms != null" data-reported-delay>
                <span class="text-text-secondary">{{ t('drawer.status.reportedDelay') }}</span>
                <p class="mt-1 text-text-primary" :title="t('drawer.status.reportedDelayHint')">
                  {{ device.bluetooth.reported_delay_ms.toFixed(1) }} ms
                </p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.sampleRate') }}</span>
                <p class="mt-1 text-text-primary">{{ device.audio.format ?? '—' }}</p>
              </div>
            </div>
            <div v-if="serverInfo" data-server-details class="space-y-2 text-sm">
              <span class="text-text-secondary">{{ t('drawer.server.title') }}</span>
              <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                <template v-for="row in serverInfo.rows" :key="row.key">
                  <dt class="text-text-secondary">{{ t(`drawer.server.${row.key}`) }}</dt>
                  <dd class="min-w-0 break-all text-text-primary" :class="row.mono ? 'font-mono text-xs' : ''">{{ row.value }}</dd>
                </template>
              </dl>
              <p v-if="serverInfo.foreign" data-server-warning class="rounded-lg bg-warning/10 p-3 text-warning-text">
                {{ t('drawer.server.foreign', { where: serverInfo.where }) }}
              </p>
            </div>
            <div v-if="refusedServers.length" data-refused-servers class="space-y-1 text-sm">
              <span class="text-text-secondary">{{ t('drawer.server.refusedTitle') }}</span>
              <ul class="space-y-0.5">
                <li v-for="r in refusedServers" :key="r.server_id ?? r.host ?? ''" class="text-text-primary">
                  {{ r.name ? `${r.host} (${r.name})` : r.host }}
                  <span class="text-text-secondary">· {{ t('drawer.server.refusedCount', { n: r.count }) }}</span>
                </li>
              </ul>
            </div>
            <div v-if="device.last_error" class="rounded-lg bg-error/10 p-3 text-sm text-error">
              {{ device.last_error }}
            </div>

            <!-- Release/Reclaim + BT Info actions -->
            <div class="flex items-center gap-2 border-t border-border pt-3">
              <SbButton
                v-if="!pulse && !released"
                variant="outline"
                size="sm"
                :loading="managementLoading"
                @click="onRelease(true)"
              >
                {{ t('bluetooth.release') }}
              </SbButton>
              <SbButton
                v-else-if="!pulse"
                variant="outline"
                size="sm"
                :loading="managementLoading"
                @click="onRelease(false)"
              >
                {{ t('bluetooth.reclaim') }}
              </SbButton>
              <SbButton v-if="!pulse" variant="outline" size="sm" @click="onBtInfo">
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
                <SbDropdownItem v-if="canDo(device, 'reconnect')" @click="runAction('reconnect')">{{ t('drawer.actions.reconnect') }}</SbDropdownItem>
                <SbDropdownItem v-if="canDo(device, 'repair')" @click="runAction('repair')">{{ t('drawer.actions.repair') }}</SbDropdownItem>
                <SbDropdownItem v-if="canDo(device, 'pairing_window')" @click="runAction('pairing')">{{ t('drawer.actions.pairing') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('unmute')">{{ t('drawer.actions.unmute') }}</SbDropdownItem>
                <SbDropdownItem v-if="!pulse" @click="runAction('release')">{{ t('drawer.actions.release') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('resume')">{{ t('drawer.actions.resume') }}</SbDropdownItem>
              </SbDropdown>
              <SbBadge v-if="!pulse && released" tone="warning" size="sm">
                {{ releaseKind(device) === 'auto' ? t('device.status.autoReleased') : t('bluetooth.released') }}
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
                <template v-if="!pulse">
                  <dt class="text-text-secondary">{{ t('diagnostics.codec') }}</dt>
                  <dd class="text-text-primary">{{ device.bluetooth?.codec_name ?? '—' }}</dd>
                </template>
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
    <div v-else-if="disabledTarget" class="text-sm">
      <DeviceSettingsPanel :device="disabledTarget" />
    </div>

    <!-- BT Device Info Modal -->
    <BtDeviceInfoModal
      v-if="device"
      :mac="device.bluetooth?.mac ?? ''"
      :adapter="device.bluetooth?.adapter.mac ?? ''"
      :open="btInfoOpen"
      @update:open="btInfoOpen = $event"
    />
  </SbDrawer>
</template>
