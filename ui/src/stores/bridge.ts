import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSSE } from '@/composables/useSSE'
import { getStatus } from '@/api/status'
import type {
  BridgeSnapshot,
  DeviceSnapshot,
  SyncGroup,
  AdapterInfo,
} from '@/api/types'

export const useBridgeStore = defineStore('bridge', () => {
  const snapshot = ref<BridgeSnapshot | null>(null)
  const loading = ref(true)
  const sseConnected = ref(false)

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
    )
    sseConnected.value = connected.value
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
  }
})
