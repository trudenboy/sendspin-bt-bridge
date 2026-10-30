import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  forgetBtDevice,
  getAdapters,
  getBtDeviceInfo,
  getKnownDevices,
  setAdapterPower,
  startPairing,
  startReset,
  startScan as apiStartScan,
  type PairOptions,
} from '@/api/bluetooth'
import { ApiError } from '@/api/client'
import { getConfig, saveConfig } from '@/api/config'
import { cancelJob } from '@/api/jobs'
import { useJobsStore } from './jobs'
import type { Adapter, BtDeviceInfo, Job, KnownDevice, ScanDevice, ScanResult } from '@/api/types'

export const useBluetoothStore = defineStore('bluetooth', () => {
  const adapters = ref<Adapter[]>([])
  /** Set by guidance actions; the devices page opens its scan dialog and clears it. */
  const scanRequested = ref(false)
  const scanJob = ref<Job | null>(null)
  const scanResults = ref<ScanDevice[]>([])
  const scanError = ref<string | null>(null)
  const scanning = ref(false)
  const pairing = ref(false)
  const pairTarget = ref<string | null>(null)
  const pairError = ref<string | null>(null)
  const scanStartedAt = ref<number | null>(null)
  const pairStartedAt = ref<number | null>(null)
  const pairJob = ref<Job | null>(null)
  /** Cancel was asked and the bridge has not finished winding the job down yet. */
  const cancelling = ref(false)

  const pairedDevices = ref<KnownDevice[]>([])
  const loadingPaired = ref(false)

  const btDeviceInfo = ref<BtDeviceInfo | null>(null)
  const loadingInfo = ref(false)

  async function fetchAdapters() {
    adapters.value = await getAdapters()
  }

  /** Scan one adapter (about 15 s); the bridge refuses a second scan or one too soon. */
  async function startScan(adapter: string, audioOnly = true) {
    scanning.value = true
    scanStartedAt.value = Date.now()
    cancelling.value = false
    scanResults.value = []
    scanError.value = null
    try {
      const job = await apiStartScan(adapter, audioOnly)
      scanJob.value = job
      const finished = await useJobsStore().waitFor(job)
      scanJob.value = finished
      if (finished.status === 'succeeded') scanResults.value = (finished.result as ScanResult).devices
      else if (finished.status !== 'cancelled') scanError.value = finished.error?.detail ?? 'Scan failed'
    } catch (e) {
      scanError.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      scanning.value = false
      scanStartedAt.value = null
      cancelling.value = false
    }
  }

  /** Pair and trust; resolves ``true`` once BlueZ holds the bond. */
  async function pairDevice(mac: string, opts: PairOptions = {}) {
    pairing.value = true
    pairTarget.value = mac
    pairStartedAt.value = Date.now()
    cancelling.value = false
    pairError.value = null
    try {
      const job = await startPairing(mac, opts)
      pairJob.value = job
      const finished = await useJobsStore().waitFor(job)
      if (finished.status !== 'succeeded' && finished.status !== 'cancelled') pairError.value = finished.error?.detail ?? 'Pairing failed'
      return finished.status === 'succeeded'
    } catch (e) {
      pairError.value = e instanceof ApiError ? e.message : String(e)
      return false
    } finally {
      pairing.value = false
      pairTarget.value = null
      pairStartedAt.value = null
      pairJob.value = null
      cancelling.value = false
    }
  }

  /** Ask the bridge to stop the running scan; the job ends as cancelled. */
  async function cancelScan() {
    if (!scanJob.value || cancelling.value) return
    cancelling.value = true
    try {
      await cancelJob(scanJob.value.id)
    } catch {
      cancelling.value = false
    }
  }

  async function cancelPairing() {
    if (!pairJob.value || cancelling.value) return
    cancelling.value = true
    try {
      await cancelJob(pairJob.value.id)
    } catch {
      cancelling.value = false
    }
  }

  async function resetReconnect(mac: string, adapter?: string) {
    return useJobsStore().waitFor(await startReset(mac, { adapter }))
  }

  async function setPower(adapterId: string, on: boolean) {
    await setAdapterPower(adapterId, on)
    await fetchAdapters()
  }

  async function fetchPairedDevices() {
    loadingPaired.value = true
    try {
      pairedDevices.value = await getKnownDevices()
    } catch {
      pairedDevices.value = []
    } finally {
      loadingPaired.value = false
    }
  }

  async function fetchBtDeviceInfo(mac: string, adapter = '') {
    loadingInfo.value = true
    btDeviceInfo.value = null
    try {
      btDeviceInfo.value = (await getBtDeviceInfo(mac, adapter)) as BtDeviceInfo
    } finally {
      loadingInfo.value = false
    }
  }

  /**
   * Make a paired device a bridge speaker: append it to the configuration
   * (the whole document is saved; the bridge starts the player live).
   */
  async function addToBridge(mac: string, name: string, adapter = '', extra: { room?: string } = {}) {
    const config = (await getConfig()) as Record<string, unknown>
    const players = (config.players as Record<string, unknown>[] | undefined) ?? []
    const taken = players.some((p) => {
      const backend = p.backend as { type?: string; mac?: string } | undefined
      return backend?.type === 'bluetooth_a2dp' && String(backend.mac ?? '').toUpperCase() === mac.toUpperCase()
    })
    if (taken) return false
    // No id: the bridge assigns it.
    const player: Record<string, unknown> = {
      name: name || mac,
      enabled: true,
      backend: { type: 'bluetooth_a2dp', mac: mac.toUpperCase(), adapter },
    }
    if (extra.room?.trim()) player.room_name = extra.room.trim()
    await saveConfig({ ...config, players: [...players, player] } as never)
    return true
  }

  async function removePairedDevice(mac: string) {
    await forgetBtDevice(mac)
    pairedDevices.value = pairedDevices.value.filter((d) => d.mac !== mac)
  }

  return {
    scanRequested,
    adapters,
    scanJob,
    scanResults,
    scanError,
    scanning,
    pairing,
    pairTarget,
    pairError,
    pairedDevices,
    loadingPaired,
    btDeviceInfo,
    loadingInfo,
    fetchAdapters,
    startScan,
    pairDevice,
    resetReconnect,
    scanStartedAt,
    pairStartedAt,
    cancelling,
    cancelScan,
    cancelPairing,
    setPower,
    fetchPairedDevices,
    fetchBtDeviceInfo,
    addToBridge,
    removePairedDevice,
  }
})
