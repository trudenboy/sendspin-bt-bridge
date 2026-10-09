<script setup lang="ts">
import { computed, watch, ref, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { SbDrawer, SbTabs, SbTimeline, SbSignalPath, SbBadge, SbButton, SbToggle } from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import BtDeviceInfoModal from '@/components/bluetooth/BtDeviceInfoModal.vue'
import { setAdapterPower } from '@/api/bluetooth'
import { getConfig, saveConfig } from '@/api/config'
import type { Device } from '@/api/types'
import { Power, RotateCw, Save, Info } from 'lucide-vue-next'

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
const saving = ref(false)
const editing = ref(false)
const btInfoOpen = ref(false)

const editForm = reactive({
  player_name: '',
  adapter: '',
  listen_port: '' as string | number,
  static_delay_ms: '' as string | number,
})

const device = computed<Device | undefined>(() => (props.deviceId ? bridge.deviceById(props.deviceId) : undefined))
const released = computed(() => device.value?.bluetooth.management_enabled === false)
const adapterId = computed(() => device.value?.bluetooth.adapter.hci || '')
const drawerTitle = computed(() => device.value?.name ?? '')
const events = computed(() => device.value?.recent_events ?? [])

const tabs = computed(() => [
  { id: 'status', label: t('drawer.tabs.status') },
  { id: 'config', label: t('drawer.tabs.config') },
  { id: 'events', label: t('drawer.tabs.events'), badge: events.value.length || undefined },
  { id: 'signal', label: t('drawer.tabs.signal') },
])

const availableAdapters = computed(() => bridge.adapters.map((a) => a.id))

const configDirty = computed(() => {
  const d = device.value
  if (!d) return false
  return (
    editForm.player_name !== (d.name ?? '') ||
    editForm.adapter !== adapterId.value ||
    String(editForm.listen_port) !== String(d.sendspin.listen_port ?? '') ||
    String(editForm.static_delay_ms) !== String(d.audio.static_delay_ms ?? '')
  )
})

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

function startEditing() {
  const d = device.value
  if (!d) return
  editForm.player_name = d.name ?? ''
  editForm.adapter = adapterId.value
  editForm.listen_port = d.sendspin.listen_port ?? ''
  editForm.static_delay_ms = d.audio.static_delay_ms ?? ''
  editing.value = true
}

function cancelEditing() {
  editing.value = false
}

/** Edits this speaker's entry in the full configuration and saves the whole document. */
async function saveDeviceConfig() {
  const d = device.value
  const mac = d?.bluetooth.mac?.toUpperCase()
  if (!d || !mac) return
  saving.value = true
  try {
    const config = (await getConfig()) as Record<string, unknown>
    const entries = ((config.BLUETOOTH_DEVICES as Record<string, unknown>[] | undefined) ?? []).map((entry) => {
      if (String(entry.mac ?? '').toUpperCase() !== mac) return entry
      const updated: Record<string, unknown> = { ...entry, player_name: editForm.player_name }
      if (editForm.adapter) updated.adapter = editForm.adapter
      else delete updated.adapter
      if (editForm.listen_port !== '') updated.listen_port = Number(editForm.listen_port)
      else delete updated.listen_port
      if (editForm.static_delay_ms !== '') updated.static_delay_ms = Number(editForm.static_delay_ms)
      return updated
    })
    await saveConfig({ ...config, BLUETOOTH_DEVICES: entries } as never)
    notifications.success(t('drawer.config.saved'))
    editing.value = false
  } catch {
    notifications.error(t('drawer.config.saveFailed'))
  } finally {
    saving.value = false
  }
}

async function onToggleEnabled() {
  const d = device.value
  if (!d) return
  try {
    await deviceStore.setEnabled(d.id, !d.enabled)
  } catch {
    notifications.error(t('device.actions.enableFailed'))
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

watch(
  () => props.deviceId,
  () => {
    editing.value = false
  },
)
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
              <SbBadge v-if="released" tone="warning" size="sm">
                {{ t('bluetooth.released') }}
              </SbBadge>
            </div>
          </div>
        </template>

        <!-- Config tab -->
        <template #config>
          <div class="space-y-4 py-4 text-sm">
            <!-- Read-only mode -->
            <div v-if="!editing" class="grid grid-cols-2 gap-3">
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.name') }}</span>
                <p class="mt-1 text-text-primary">{{ device.name }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.mac') }}</span>
                <p class="mt-1 font-mono text-text-primary">{{ device.bluetooth.mac }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.adapter') }}</span>
                <p class="mt-1 text-text-primary">{{ adapterId || '—' }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.port') }}</span>
                <p class="mt-1 text-text-primary">{{ device.sendspin.listen_port ?? '—' }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.delay') }}</span>
                <p class="mt-1 text-text-primary">
                  {{ `${device.audio.static_delay_ms} ms` }}
                </p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.enabled') }}</span>
                <div class="mt-1">
                  <SbToggle
                    :model-value="device.enabled"
                    :label="device.enabled ? t('common.yes') : t('common.no')"
                    @update:model-value="onToggleEnabled"
                  />
                </div>
              </div>
            </div>

            <!-- Edit mode -->
            <div v-else class="space-y-3">
              <div>
                <label class="mb-1 block text-xs text-text-secondary">{{ t('drawer.config.name') }}</label>
                <input
                  v-model="editForm.player_name"
                  type="text"
                  class="w-full rounded-lg border border-border bg-surface-primary px-2.5 py-1.5 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div>
                <label class="mb-1 block text-xs text-text-secondary">{{ t('drawer.config.mac') }}</label>
                <p class="mt-1 font-mono text-text-secondary">{{ device.bluetooth.mac }}</p>
              </div>
              <div>
                <label class="mb-1 block text-xs text-text-secondary">{{ t('drawer.config.adapter') }}</label>
                <select
                  v-model="editForm.adapter"
                  class="w-full rounded-lg border border-border bg-surface-primary px-2.5 py-1.5 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">{{ t('drawer.config.autoAdapter') }}</option>
                  <option v-for="a in availableAdapters" :key="a" :value="a">{{ a }}</option>
                </select>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="mb-1 block text-xs text-text-secondary">{{ t('drawer.config.port') }}</label>
                  <input
                    v-model="editForm.listen_port"
                    type="number"
                    class="w-full rounded-lg border border-border bg-surface-primary px-2.5 py-1.5 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    :placeholder="t('drawer.config.autoPort')"
                  />
                </div>
                <div>
                  <label class="mb-1 block text-xs text-text-secondary">{{ t('drawer.config.delay') }}</label>
                  <input
                    v-model="editForm.static_delay_ms"
                    type="number"
                    class="w-full rounded-lg border border-border bg-surface-primary px-2.5 py-1.5 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    placeholder="ms"
                  />
                </div>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-xs text-text-secondary">{{ t('drawer.config.enabled') }}</span>
                <SbToggle
                  :model-value="device.enabled"
                  :label="device.enabled ? t('common.yes') : t('common.no')"
                  @update:model-value="onToggleEnabled"
                />
              </div>
            </div>

            <!-- Edit/Save buttons -->
            <div class="flex items-center gap-2">
              <SbButton
                v-if="!editing"
                variant="outline"
                size="sm"
                @click="startEditing"
              >
                {{ t('drawer.config.edit') }}
              </SbButton>
              <template v-else>
                <SbButton
                  size="sm"
                  :loading="saving"
                  :disabled="!configDirty"
                  @click="saveDeviceConfig"
                >
                  <template #icon-left>
                    <Save class="h-3.5 w-3.5" />
                  </template>
                  {{ t('drawer.config.save') }}
                </SbButton>
                <SbButton variant="ghost" size="sm" @click="cancelEditing">
                  {{ t('common.cancel') }}
                </SbButton>
              </template>
            </div>

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
