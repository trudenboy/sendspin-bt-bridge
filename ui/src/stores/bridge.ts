import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSSE } from '@/composables/useSSE'
import { getStatus } from '@/api/status'
import { apiPost } from '@/api/client'
import type {
  BridgeSnapshot,
  DeviceSnapshot,
  SyncGroup,
  AdapterInfo,
} from '@/api/types'

export type RestartState = 'idle' | 'stopping' | 'restarting' | 'ready' | 'error'

export const useBridgeStore = defineStore('bridge', () => {
  const snapshot = ref<BridgeSnapshot | null>(null)
  const loading = ref(true)
  const sseConnected = ref(false)

  /* Restart state machine */
  const restartState = ref<RestartState>('idle')
  const restartStartedAt = ref<number | null>(null)

  /* Computed from snapshot */
  const devices = computed<DeviceSnapshot[]>(
    () => snapshot.value?.devices ?? [],
  )
  const groups = computed<SyncGroup[]>(() => snapshot.value?.groups ?? [])
  const adapters = computed<AdapterInfo[]>(
    () => snapshot.value?.adapters ?? [],
  )
  const version = computed(() => snapshot.value?.version ?? '')
  const maConnected = computed(() => snapshot.value?.ma_connected ?? false)

  /* SSE connection */
  function connectSSE() {
    const { connected } = useSSE<BridgeSnapshot>(
      '/api/status/stream',
      (data) => {
        snapshot.value = data
        loading.value = false
      },
      {
        onConnect() {
          sseConnected.value = true
          if (restartState.value === 'restarting' || restartState.value === 'stopping') {
            restartState.value = 'ready'
          }
        },
        onDisconnect() {
          sseConnected.value = false
          if (restartState.value === 'stopping') {
            restartState.value = 'restarting'
          }
        },
      },
    )
    sseConnected.value = connected.value
  }

  /* Restart lifecycle */
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
      await apiPost('/api/restart')
    } catch {
      restartState.value = 'error'
    }
  }

  /* Manual refresh */
  async function refresh() {
    try {
      snapshot.value = await getStatus()
      loading.value = false
    } catch {
      // SSE will recover
    }
  }

  return {
    snapshot,
    loading,
    sseConnected,
    devices,
    groups,
    adapters,
    version,
    maConnected,
    connectSSE,
    refresh,
    restartState,
    restartStartedAt,
    initiateRestart,
    dismissRestart,
    restart,
  }
})
