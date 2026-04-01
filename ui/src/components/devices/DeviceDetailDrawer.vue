<script setup lang="ts">
import { computed, watch, ref, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { SbDrawer, SbTabs, SbTimeline, SbSignalPath, SbBadge, SbButton, SbToggle } from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import { queryEvents } from '@/api/events'
import { toggleAdapterPower, rebootAdapter } from '@/api/devices'
import { saveConfig } from '@/api/config'
import type { DeviceSnapshot, EventRecord } from '@/api/types'
import { Power, RotateCw, Save } from 'lucide-vue-next'

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
const events = ref<EventRecord[]>([])
const loadingEvents = ref(false)
const adapterLoading = ref(false)
const saving = ref(false)
const editing = ref(false)

const editForm = reactive({
  player_name: '',
  adapter: '',
  listen_port: '' as string | number,
  static_delay_ms: '' as string | number,
})

const device = computed<DeviceSnapshot | undefined>(() =>
  bridge.devices.find((d) => d.mac === props.deviceId),
)

const drawerTitle = computed(() => device.value?.player_name ?? '')

const tabs = computed(() => [
  { id: 'status', label: t('drawer.tabs.status') },
  { id: 'config', label: t('drawer.tabs.config') },
  { id: 'events', label: t('drawer.tabs.events'), badge: events.value.length || undefined },
  { id: 'signal', label: t('drawer.tabs.signal') },
])

const availableAdapters = computed(() => {
  const snap = bridge.snapshot
  return snap?.adapters?.map((a) => a.name ?? a.mac) ?? []
})

const configDirty = computed(() => {
  const d = device.value
  if (!d) return false
  return (
    editForm.player_name !== d.player_name ||
    editForm.adapter !== (d.adapter ?? '') ||
    String(editForm.listen_port) !== String(d.listen_port ?? '') ||
    String(editForm.static_delay_ms) !== String(d.static_delay_ms ?? '')
  )
})

const signalSegments = computed(() => {
  const d = device.value
  if (!d) return []
  return [
    {
      id: 'ma',
      label: 'Music Assistant',
      status: d.server_connected ? 'active' as const : 'inactive' as const,
    },
    {
      id: 'sendspin',
      label: 'Sendspin',
      status: d.server_connected ? 'active' as const : 'inactive' as const,
      sublabel: d.listen_port ? `port ${d.listen_port}` : undefined,
    },
    {
      id: 'subprocess',
      label: t('drawer.signal.subprocess'),
      status: d.connected ? 'active' as const : 'inactive' as const,
    },
    {
      id: 'sink',
      label: d.audio_sink ?? 'Audio Sink',
      status: d.audio_streaming ? 'active' as const : d.connected ? 'inactive' as const : 'error' as const,
    },
    {
      id: 'speaker',
      label: d.player_name,
      status: d.audio_streaming ? 'active' as const : d.connected ? 'inactive' as const : 'error' as const,
    },
  ]
})

const timelineEvents = computed(() =>
  events.value.map((e) => ({
    id: `${e.at}-${e.event_type}`,
    timestamp: new Date(e.at).toLocaleString(),
    title: e.event_type,
    description: e.payload ? JSON.stringify(e.payload) : undefined,
    type: (e.category === 'error' ? 'error' : 'info') as 'info' | 'error',
  })),
)

function onDrawerUpdate(...args: unknown[]) {
  emit('update:open', Boolean(args[0]))
}

function startEditing() {
  const d = device.value
  if (!d) return
  editForm.player_name = d.player_name
  editForm.adapter = d.adapter ?? ''
  editForm.listen_port = d.listen_port ?? ''
  editForm.static_delay_ms = d.static_delay_ms ?? ''
  editing.value = true
}

function cancelEditing() {
  editing.value = false
}

async function saveDeviceConfig() {
  const d = device.value
  if (!d) return
  saving.value = true
  try {
    const devices = bridge.snapshot?.devices ?? []
    const updatedDevices = devices.map((dev) => {
      if (dev.mac !== d.mac) {
        return {
          mac: dev.mac,
          player_name: dev.player_name,
          adapter: dev.adapter,
          enabled: dev.enabled,
          listen_port: dev.listen_port,
          static_delay_ms: dev.static_delay_ms,
        }
      }
      return {
        mac: dev.mac,
        player_name: editForm.player_name,
        adapter: editForm.adapter || undefined,
        enabled: dev.enabled,
        listen_port: editForm.listen_port ? Number(editForm.listen_port) : undefined,
        static_delay_ms: editForm.static_delay_ms ? Number(editForm.static_delay_ms) : undefined,
      }
    })
    await saveConfig({ BLUETOOTH_DEVICES: updatedDevices })
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
    await deviceStore.setEnabled(d.mac, !d.enabled)
  } catch {
    notifications.error(t('device.actions.enableFailed'))
  }
}

async function onAdapterPower() {
  const adapter = device.value?.adapter
  if (!adapter) return
  adapterLoading.value = true
  try {
    await toggleAdapterPower(adapter)
    notifications.success(t('adapter.powerToggled'))
  } catch {
    notifications.error(t('adapter.powerFailed'))
  } finally {
    adapterLoading.value = false
  }
}

async function onAdapterReboot() {
  const adapter = device.value?.adapter
  if (!adapter) return
  adapterLoading.value = true
  try {
    await rebootAdapter(adapter)
    notifications.success(t('adapter.rebooted'))
  } catch {
    notifications.error(t('adapter.rebootFailed'))
  } finally {
    adapterLoading.value = false
  }
}

watch(
  () => props.deviceId,
  async (id) => {
    editing.value = false
    if (!id) return
    loadingEvents.value = true
    try {
      events.value = await queryEvents({ player_id: id, limit: 50 })
    } catch {
      events.value = []
    } finally {
      loadingEvents.value = false
    }
  },
  { immediate: true },
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
                  <DeviceStatusBadge :state="device.player_state ?? 'OFFLINE'" />
                </div>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.backendType') }}</span>
                <div class="mt-1">
                  <SbBadge tone="neutral" size="sm">
                    {{ device.backend_info?.type ?? 'bluetooth_a2dp' }}
                  </SbBadge>
                </div>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.audioSink') }}</span>
                <p class="mt-1 font-mono text-xs text-text-primary">
                  {{ device.audio_sink ?? '—' }}
                </p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.connected') }}</span>
                <p class="mt-1 text-text-primary">
                  {{ device.connected ? t('common.yes') : t('common.no') }}
                </p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.codec') }}</span>
                <p class="mt-1 text-text-primary">{{ device.codec ?? '—' }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.status.sampleRate') }}</span>
                <p class="mt-1 text-text-primary">{{ device.sample_rate ?? '—' }}</p>
              </div>
            </div>
            <div v-if="device.error" class="rounded-lg bg-red-50 p-3 text-sm text-error dark:bg-red-900/10">
              {{ device.error }}
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
                <p class="mt-1 text-text-primary">{{ device.player_name }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.mac') }}</span>
                <p class="mt-1 font-mono text-text-primary">{{ device.mac }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.adapter') }}</span>
                <p class="mt-1 text-text-primary">{{ device.adapter ?? '—' }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.port') }}</span>
                <p class="mt-1 text-text-primary">{{ device.listen_port ?? '—' }}</p>
              </div>
              <div>
                <span class="text-text-secondary">{{ t('drawer.config.delay') }}</span>
                <p class="mt-1 text-text-primary">
                  {{ device.static_delay_ms != null ? `${device.static_delay_ms} ms` : '—' }}
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
                <p class="mt-1 font-mono text-text-secondary">{{ device.mac }}</p>
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
                variant="secondary"
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
            <div v-if="device.adapter" class="border-t border-border pt-3">
              <h4 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {{ t('adapter.management') }}
              </h4>
              <div class="flex items-center gap-2">
                <SbButton
                  variant="secondary"
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
                  variant="secondary"
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
            <div v-if="loadingEvents" class="py-8 text-center text-text-secondary">
              {{ t('common.loading') }}
            </div>
            <div v-else-if="timelineEvents.length === 0" class="py-8 text-center text-text-secondary">
              {{ t('drawer.events.empty') }}
            </div>
            <SbTimeline v-else :events="timelineEvents" :max-items="10" />
          </div>
        </template>

        <!-- Signal Path tab -->
        <template #signal>
          <div class="py-4">
            <SbSignalPath :segments="signalSegments" direction="vertical" />
          </div>
        </template>
      </SbTabs>
    </template>
  </SbDrawer>
</template>
