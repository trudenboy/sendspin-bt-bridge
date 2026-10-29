import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useEventStream } from '@/composables/useEventStream'
import { getStatus, restartBridge } from '@/api/status'
import { getAdapters } from '@/api/bluetooth'
import { useJobsStore } from './jobs'
import { useSyncStore } from './sync'
import type { Adapter, BridgeStatus, Device, Group, Job, SyncSession } from '@/api/types'

export type RestartState = 'idle' | 'stopping' | 'restarting' | 'ready' | 'error'

export const useBridgeStore = defineStore('bridge', () => {
  const snapshot = ref<BridgeStatus | null>(null)
  const adapters = ref<Adapter[]>([])
  const loading = ref(true)
  const sseConnected = ref(false)

  const restartState = ref<RestartState>('idle')
  const restartStartedAt = ref<number | null>(null)

  const devices = computed<Device[]>(() => snapshot.value?.devices ?? [])
  const groups = computed<Group[]>(() => snapshot.value?.groups ?? [])
  const bridge = computed(() => snapshot.value?.bridge ?? null)
  const version = computed(() => snapshot.value?.bridge.version ?? '')
  const maConnected = computed(() => snapshot.value?.bridge.ma_connected ?? false)
  /** The bridge's sign-in to Music Assistant, as the bridge names it (not a player's audio link). */
  const maAccount = computed(() => snapshot.value?.bridge.music_assistant_account ?? null)

  function apply(status: BridgeStatus) {
    snapshot.value = status
    loading.value = false
  }

  const stream = useEventStream({
    types: ['status', 'job', 'sync'],
    onEvent(event) {
      if (event.type === 'status') apply(event.data as BridgeStatus)
      else if (event.type === 'job') useJobsStore().update(event.data as Job)
      else if (event.type === 'sync') useSyncStore().applyEvent(event.data as SyncSession)
    },
    onConnect() {
      sseConnected.value = true
      // Sync events are not replayed: catch up on what was missed while the stream was down.
      void useSyncStore().resync()
      if (restartState.value === 'restarting' || restartState.value === 'stopping') {
        restartState.value = 'ready'
        void refreshAdapters()
      }
    },
    onDisconnect() {
      sseConnected.value = false
      if (restartState.value === 'stopping') restartState.value = 'restarting'
    },
    onPoll: () => void refresh(),
  })

  function connectSSE() {
    stream.connect()
    void refresh()
    void refreshAdapters()
  }

  function initiateRestart() {
    restartState.value = 'stopping'
    restartStartedAt.value = Date.now()
  }

  function dismissRestart() {
    restartState.value = 'idle'
    restartStartedAt.value = null
  }

  async function restart() {
    initiateRestart()
    try {
      await restartBridge()
    } catch {
      restartState.value = 'error'
    }
  }

  async function refresh() {
    try {
      apply(await getStatus())
    } catch {
      /* the event stream will catch up */
    }
  }

  async function refreshAdapters() {
    try {
      adapters.value = await getAdapters()
    } catch {
      adapters.value = []
    }
  }

  function deviceById(id: string): Device | undefined {
    return devices.value.find((d) => d.id === id)
  }

  return {
    snapshot,
    bridge,
    loading,
    sseConnected,
    devices,
    groups,
    adapters,
    version,
    maConnected,
    maAccount,
    connectSSE,
    disconnectSSE: stream.disconnect,
    refresh,
    refreshAdapters,
    deviceById,
    restartState,
    restartStartedAt,
    initiateRestart,
    dismissRestart,
    restart,
  }
})
