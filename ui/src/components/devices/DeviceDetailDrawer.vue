<script setup lang="ts">
import { speakerName } from '@/utils/speakerName'
import { confirmAction } from '@/composables/useActionPolicy'
import { useReversibleActions } from '@/composables/useReversibleActions'
import { actionFailure } from '@/utils/actionFailure'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { isDeviceConnected, isPulseSink } from '@/utils/deviceBackend'
import { SbDrawer, SbTabs, SbTimeline, SbSignalPath, SbBadge, SbButton, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import StatusState from '@/components/StatusState.vue'
import BtDeviceInfoModal from '@/components/bluetooth/BtDeviceInfoModal.vue'
import { openPairingWindow, reconnectDevice, repairDevice, setPowerSave, unmuteSink } from '@/api/devices'
import { ApiError } from '@/api/client'
import DeviceSettingsPanel from './DeviceSettingsPanel.vue'
import DeviceTimingPanel from './DeviceTimingPanel.vue'
import type { Device } from '@/api/types'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { canDo } from '@/utils/supportedActions'
import { releaseKind } from '@/utils/deviceStatusText'
import { ChevronDown } from 'lucide-vue-next'

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

const activeTab = ref('overview')
const managementLoading = ref(false)
const btInfoOpen = ref(false)
const reversible = useReversibleActions()

const device = computed<Device | undefined>(() => (props.deviceId ? bridge.deviceById(props.deviceId) : undefined))
const pulse = computed(() => (device.value ? isPulseSink(device.value) : false))
const released = computed(() => device.value?.bluetooth?.management_enabled === false)
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
  { id: 'overview', label: t('drawer.tabs.overview') },
  { id: 'settings', label: t('drawer.tabs.config') },
  { id: 'timing', label: t('drawer.tabs.timing') },
  { id: 'history', label: t('drawer.tabs.history') },
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
    { id: 'ma', label: t('status.kind.ma_account'), status: bridge.maConnected ? ('active' as const) : ('inactive' as const) },
    {
      id: 'sendspin',
      label: t('status.kind.audio_link'),
      status: d.playback.server_connected ? ('active' as const) : ('inactive' as const),
      sublabel: d.sendspin.listen_port ? t('drawer.signal.port', { port: d.sendspin.listen_port }) : undefined,
    },
    {
      id: 'subprocess',
      label: t('drawer.signal.subprocess'),
      status: d.playback.connected ? ('active' as const) : ('inactive' as const),
    },
    { id: 'sink', label: d.audio.sink_name ?? t('diagnostics.audioSink'), status: sinkStatus },
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

const recentEvents = computed(() => timelineEvents.value.slice(0, 3))

function onDrawerUpdate(...args: unknown[]) {
  emit('update:open', Boolean(args[0]))
}

/** Rarely needed recovery actions, kept in one menu (Music Assistant keeps toolbars minimal). */
const actionBusy = ref(false)
async function runAction(kind: 'reconnect' | 'repair' | 'pairing' | 'unmute' | 'release' | 'resume') {
  const d = device.value
  if (!d) return
  if (kind === 'repair') {
    const name = speakerName(d.name)
    const ok = await confirmAction('speaker.repair', {
      title: t('speaker.repairTitle', { name }),
      consequences: [t('consequence.disconnects', { names: name }), t('consequence.repairs')],
      confirmLabel: t('drawer.actions.repair'),
    })
    if (!ok) return
  }
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
    if (release) {
      await reversible.handOver(d.id, speakerName(d.name))
      return
    }
    await deviceStore.release(d.id, false)
  } catch (e) {
    notifications.error(actionFailure(t, release ? 'release' : 'claim', speakerName(d.name), e))
  } finally {
    managementLoading.value = false
  }
}

function onBtInfo() {
  btInfoOpen.value = true
}

/** The one thing to do about this speaker right now (the rest is under Troubleshoot). */
const primary = computed(() => {
  const d = device.value
  if (!d || pulse.value) return null
  if (released.value) return { kind: 'reclaim' as const, label: t('bluetooth.reclaim'), recovery: true }
  if (!isDeviceConnected(d) && canDo(d, 'reconnect')) return { kind: 'reconnect' as const, label: t('drawer.actions.reconnect'), recovery: true }
  return { kind: 'release' as const, label: t('bluetooth.release'), recovery: false }
})

function onPrimary() {
  const k = primary.value?.kind
  if (k === 'reclaim') void onRelease(false)
  else if (k === 'reconnect') void runAction('reconnect')
  else if (k === 'release') void onRelease(true)
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
      <SbTabs v-model="activeTab" :tabs="tabs" fit>
        <!-- Overview: state, the one thing to do, the audio path, the latest events -->
        <template #overview>
          <div data-overview class="space-y-5 py-4">
            <div class="flex flex-wrap items-center gap-2">
              <DeviceStatusBadge :state="deviceState(device)" :device="device" />
              <SbBadge v-if="!pulse && released" tone="warning" size="sm">
                {{ releaseKind(device) === 'auto' ? t('device.status.autoReleased') : t('bluetooth.released') }}
              </SbBadge>
            </div>

            <div class="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              <div v-if="device.links.bluetooth">
                <span class="text-text-secondary">{{ t('status.kind.bluetooth') }}</span>
                <div class="mt-1"><StatusState kind="bluetooth" :status="device.links.bluetooth" /></div>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('status.kind.audio_link') }}</span>
                <div class="mt-1"><StatusState kind="audio_link" :status="device.links.audio_link" /></div>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('status.kind.ma_account') }}</span>
                <div class="mt-1"><StatusState kind="ma_account" :status="bridge.maAccount" /></div>
              </div>
            </div>

            <div v-if="device.last_error" class="rounded-lg bg-error/10 p-3 text-sm text-error">
              {{ device.last_error }}
            </div>

            <div class="flex flex-wrap items-center gap-2">
              <SbButton
                v-if="primary"
                :variant="primary.recovery ? 'primary' : 'outline'"
                size="sm"
                data-primary-action
                :loading="managementLoading || actionBusy"
                @click="onPrimary"
              >
                {{ primary.label }}
              </SbButton>
              <SbDropdown align="left">
                <template #trigger>
                  <SbButton variant="outline" size="sm" :loading="actionBusy" data-troubleshoot>
                    {{ t('drawer.troubleshoot.label') }}
                    <template #icon-right><ChevronDown class="h-3.5 w-3.5" /></template>
                  </SbButton>
                </template>
                <SbDropdownItem v-if="canDo(device, 'reconnect')" @click="runAction('reconnect')">{{ t('drawer.actions.reconnect') }}</SbDropdownItem>
                <SbDropdownItem v-if="canDo(device, 'repair')" @click="runAction('repair')">{{ t('drawer.actions.repair') }}</SbDropdownItem>
                <SbDropdownItem v-if="canDo(device, 'pairing_window')" @click="runAction('pairing')">{{ t('drawer.actions.pairing') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('unmute')">{{ t('drawer.actions.unmute') }}</SbDropdownItem>
                <SbDropdownItem v-if="!pulse" @click="runAction('release')">{{ t('drawer.actions.release') }}</SbDropdownItem>
                <SbDropdownItem @click="runAction('resume')">{{ t('drawer.actions.resume') }}</SbDropdownItem>
                <SbDropdownItem v-if="!pulse" @click="onBtInfo">{{ t('bluetooth.info') }}</SbDropdownItem>
              </SbDropdown>
            </div>

            <div v-if="serverInfo && (technicalDetails || serverInfo.foreign)" data-server-details class="space-y-2 text-sm">
              <span v-if="technicalDetails" class="text-text-secondary">{{ t('drawer.server.title') }}</span>
              <dl v-if="technicalDetails" class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
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

            <!-- The audio path, from Music Assistant to the speaker -->
            <section v-if="technicalDetails" data-audio-path class="border-t border-border pt-4">
              <h4 class="mb-3 text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {{ t('drawer.overview.audioPath') }}
              </h4>
              <SbSignalPath :segments="signalSegments" direction="vertical" />
              <dl class="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                <dt class="text-text-secondary">{{ t('diagnostics.audioSink') }}</dt>
                <dd class="min-w-0 break-all font-mono text-xs text-text-primary">{{ device.audio.sink_name ?? '—' }}</dd>
                <template v-if="!pulse">
                  <dt class="text-text-secondary">{{ t('diagnostics.codec') }}</dt>
                  <dd class="text-text-primary">{{ device.bluetooth?.codec_name ?? '—' }}</dd>
                  <div v-if="device.bluetooth?.reported_delay_ms != null" data-reported-delay class="contents">
                    <dt class="text-text-secondary">{{ t('drawer.status.reportedDelay') }}</dt>
                    <dd class="text-text-primary" :title="t('drawer.status.reportedDelayHint')">{{ device.bluetooth.reported_delay_ms.toFixed(1) }} ms</dd>
                  </div>
                </template>
                <dt class="text-text-secondary">{{ t('diagnostics.sampleRate') }}</dt>
                <dd class="text-text-primary">{{ device.audio.format ?? '—' }}</dd>
                <dt class="text-text-secondary">{{ t('diagnostics.reanchorCount') }}</dt>
                <dd class="text-text-primary">{{ device.timing.reanchor_count > 0 ? t('drawer.signal.corrections', { n: device.timing.reanchor_count }, device.timing.reanchor_count) : '—' }}</dd>
              </dl>
            </section>

            <section class="border-t border-border pt-4">
              <h4 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {{ t('drawer.overview.events') }}
              </h4>
              <p v-if="recentEvents.length === 0" class="text-sm text-text-secondary">{{ t('drawer.events.empty') }}</p>
              <ul v-else class="space-y-1.5 text-sm">
                <li v-for="e in recentEvents" :key="e.id" data-overview-event class="flex flex-col gap-0.5">
                  <span class="text-text-primary">{{ e.description ?? e.title }}</span>
                  <span class="text-xs text-text-secondary">{{ e.timestamp }}</span>
                </li>
              </ul>
              <button
                v-if="timelineEvents.length > recentEvents.length"
                type="button"
                data-all-events
                class="mt-2 cursor-pointer text-sm font-medium text-primary-text underline underline-offset-2"
                @click="activeTab = 'history'"
              >
                {{ t('drawer.overview.allEvents') }}
              </button>
            </section>
          </div>
        </template>

        <!-- Settings: saved as you change them; adapter tools live in Settings, Bluetooth -->
        <template #settings>
          <div class="text-sm">
            <DeviceSettingsPanel :device="device" />
          </div>
        </template>

        <template #timing>
          <DeviceTimingPanel :device="device" />
        </template>

        <template #history>
          <div class="py-4">
            <div v-if="timelineEvents.length === 0" class="py-8 text-center text-text-secondary">
              {{ t('drawer.events.empty') }}
            </div>
            <SbTimeline v-else :events="timelineEvents" :max-items="20" />
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
