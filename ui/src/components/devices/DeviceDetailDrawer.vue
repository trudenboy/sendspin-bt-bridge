<script setup lang="ts">
import { computed, watch, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { SbDrawer, SbTabs, SbTimeline, SbSignalPath, SbBadge } from '@/kit'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import { queryEvents } from '@/api/events'
import type { DeviceSnapshot, EventRecord } from '@/api/types'

const props = defineProps<{
  deviceId: string | null
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const bridge = useBridgeStore()

const activeTab = ref('status')
const events = ref<EventRecord[]>([])
const loadingEvents = ref(false)

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

watch(
  () => props.deviceId,
  async (id) => {
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
          <div class="space-y-3 py-4 text-sm">
            <div class="grid grid-cols-2 gap-3">
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
                <p class="mt-1 text-text-primary">
                  {{ device.enabled ? t('common.yes') : t('common.no') }}
                </p>
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
