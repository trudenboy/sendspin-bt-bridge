import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getAdapters,
  getPairedDevices as apiGetPairedDevices,
  getBtDeviceInfo as apiGetBtDeviceInfo,
  setDeviceManagement as apiSetDeviceManagement,
  resetAndReconnect as apiResetAndReconnect,
  startScan as apiStartScan,
  getScanResult,
  pairDevice as apiPairDevice,
  removeDevice as apiRemoveDevice,
  rebootAdapter as apiRebootAdapter,
} from '@/api/devices'
import type { AdapterInfo, BtDeviceInfo, BtScanDevice, PairedDevice } from '@/api/types'

export const useBluetoothStore = defineStore('bluetooth', () => {
  const adapters = ref<AdapterInfo[]>([])
  const scanJobId = ref<string | null>(null)
  const scanResults = ref<BtScanDevice[]>([])
  const scanning = ref(false)
  const pairing = ref(false)
  const pairTarget = ref<string | null>(null)

  const pairedDevices = ref<PairedDevice[]>([])
  const loadingPaired = ref(false)

  const btDeviceInfo = ref<BtDeviceInfo | null>(null)
  const loadingInfo = ref(false)

  const managementLoading = ref(false)
  const resetReconnectJobId = ref<string | null>(null)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function fetchAdapters() {
    adapters.value = await getAdapters()
  }

  async function startScan(adapterId?: string) {
    scanning.value = true
    scanResults.value = []
    stopPolling()

    try {
      const job = await apiStartScan(adapterId)
      scanJobId.value = job.job_id

      pollTimer = setInterval(async () => {
        if (!scanJobId.value) return
        try {
          const res = await getScanResult(scanJobId.value)
          if (res.status === 'completed') {
            scanResults.value = res.result ?? []
            scanning.value = false
            stopPolling()
          } else if (res.status === 'failed') {
            scanning.value = false
            stopPolling()
          }
        } catch {
          scanning.value = false
          stopPolling()
        }
      }, 1000)
    } catch {
      scanning.value = false
    }
  }

  async function getScanResults() {
    if (!scanJobId.value) return
    const res = await getScanResult(scanJobId.value)
    if (res.status === 'completed') {
      scanResults.value = res.result ?? []
    }
  }

  async function pairDevice(mac: string, adapterId?: string) {
    pairing.value = true
    pairTarget.value = mac
    try {
      await apiPairDevice(mac, adapterId)
    } finally {
      pairing.value = false
      pairTarget.value = null
    }
  }

  async function rebootAdapter(adapterId: string) {
    await apiRebootAdapter(adapterId)
  }

  async function fetchPairedDevices() {
    loadingPaired.value = true
    try {
      pairedDevices.value = await apiGetPairedDevices()
    } catch {
      pairedDevices.value = []
    } finally {
      loadingPaired.value = false
    }
  }

  async function fetchBtDeviceInfo(mac: string) {
    loadingInfo.value = true
    btDeviceInfo.value = null
    try {
      btDeviceInfo.value = await apiGetBtDeviceInfo(mac)
    } finally {
      loadingInfo.value = false
    }
  }

  async function setManagement(playerName: string, managed: boolean) {
    managementLoading.value = true
    try {
      await apiSetDeviceManagement(playerName, managed)
    } finally {
      managementLoading.value = false
    }
  }

  async function resetReconnect(mac: string, adapter?: string) {
    const res = await apiResetAndReconnect(mac, adapter)
    resetReconnectJobId.value = res.job_id
    return res.job_id
  }

  async function removePairedDevice(mac: string) {
    await apiRemoveDevice(mac)
    pairedDevices.value = pairedDevices.value.filter((d) => d.mac !== mac)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return {
    adapters,
    scanJobId,
    scanResults,
    scanning,
    pairing,
    pairTarget,
    pairedDevices,
    loadingPaired,
    btDeviceInfo,
    loadingInfo,
    managementLoading,
    resetReconnectJobId,
    fetchAdapters,
    startScan,
    getScanResults,
    pairDevice,
    rebootAdapter,
    fetchPairedDevices,
    fetchBtDeviceInfo,
    setManagement,
    resetReconnect,
    removePairedDevice,
    stopPolling,
  }
})
