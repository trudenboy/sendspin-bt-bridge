import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useEventStream } from '@/composables/useEventStream'
import { getStatus, restartBridge } from '@/api/status'
import { getAdapters } from '@/api/bluetooth'
import { useJobsStore } from './jobs'
import { useSourceStore } from './source'
import { useSyncStore } from './sync'
import type { Adapter, BridgeStatus, Device, Group, Job, SyncSession } from '@/api/types'

export type RestartState = 'idle' | 'stopping' | 'restarting' | 'ready' | 'error'
/** Why the bridge restarts: asked for, or because an update was installed. */
export type RestartReason = 'restart' | 'update'
/**
 * What a fresh status read did: it applied, it was dropped because a newer read
 * already applied, or the bridge did not answer. Callers that keep a fallback
 * must tell the first two apart.
 */
export type RefreshOutcome = 'applied' | 'stale' | 'failed'

export const useBridgeStore = defineStore('bridge', () => {
  const snapshot = ref<BridgeStatus | null>(null)
  const adapters = ref<Adapter[]>([])
  const loading = ref(true)
  const sseConnected = ref(false)

  const restartState = ref<RestartState>('idle')
  const restartStartedAt = ref<number | null>(null)
  const restartReason = ref<RestartReason>('restart')

  const devices = computed<Device[]>(() => snapshot.value?.devices ?? [])
  const groups = computed<Group[]>(() => snapshot.value?.groups ?? [])
  const bridge = computed(() => snapshot.value?.bridge ?? null)
  const version = computed(() => snapshot.value?.bridge.version ?? '')
  const maConnected = computed(() => snapshot.value?.bridge.ma_connected ?? false)
  /** The bridge's sign-in to Music Assistant, as the bridge names it (not a player's audio link). */
  const maAccount = computed(() => snapshot.value?.bridge.music_assistant_account ?? null)

  /** The version the bridge ran when an update began; another version means it is done. */
  let versionBeforeUpdate = ''

  function apply(status: BridgeStatus, sourceEpoch?: number, requestedAt?: number) {
    snapshot.value = status
    // The source feed has its own owner; it is fed from this one status, never
    // from a second stream or a second read. A read carries the epoch it began
    // in, and when it began, so the source owner can drop what predates a
    // completed command and tell a post-command read from a pushed frame.
    useSourceStore().applyStatus(status, { epoch: sourceEpoch, requestedAt })
    const inProgress = restartState.value === 'stopping' || restartState.value === 'restarting'
    if (inProgress && restartReason.value === 'update' && versionBeforeUpdate && status.bridge.version !== versionBeforeUpdate) {
      restartState.value = 'ready'
      void refreshAdapters()
    }
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

  function initiateRestart(reason: RestartReason = 'restart') {
    restartReason.value = reason
    versionBeforeUpdate = reason === 'update' ? version.value : ''
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

  /** A read that started before a newer one — or before a source command — never wins. */
  let refreshSeq = 0
  let appliedSeq = 0

  /** A fresh read; ``failed`` means the bridge did not answer, so callers can keep what they had. */
  async function refresh(): Promise<RefreshOutcome> {
    const seq = ++refreshSeq
    // The source owner's epoch when this read began: an answer that predates a
    // completed source command must not restate its pairing windows.
    const sourceEpoch = useSourceStore().commandEpoch
    const requestedAt = Date.now()
    try {
      const status = await getStatus()
      if (seq < appliedSeq) return 'stale'
      appliedSeq = seq
      apply(status, sourceEpoch, requestedAt)
      return 'applied'
    } catch {
      /* the event stream will catch up */
      return 'failed'
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
    restartReason,
    initiateRestart,
    dismissRestart,
    restart,
  }
})
