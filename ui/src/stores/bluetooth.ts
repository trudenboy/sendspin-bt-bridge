import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getAdapters,
  startScan as apiStartScan,
  getScanResult,
  pairDevice as apiPairDevice,
  rebootAdapter as apiRebootAdapter,
} from '@/api/devices'
import type { AdapterInfo, BtScanDevice } from '@/api/types'

export const useBluetoothStore = defineStore('bluetooth', () => {
  const adapters = ref<AdapterInfo[]>([])
  const scanJobId = ref<string | null>(null)
  const scanResults = ref<BtScanDevice[]>([])
  const scanning = ref(false)
  const pairing = ref(false)
  const pairTarget = ref<string | null>(null)

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
    fetchAdapters,
    startScan,
    getScanResults,
    pairDevice,
    rebootAdapter,
    stopPolling,
  }
})
