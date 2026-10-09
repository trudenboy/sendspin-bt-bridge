import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  downloadBugreport as apiDownloadBugreport,
  getDiagnostics,
  getPreflight,
  getOperatorGuidance,
  getRecoveryAssistant,
} from '@/api/diagnostics'

type Payload = Record<string, unknown>

export const useDiagnosticsStore = defineStore('diagnostics', () => {
  const health = ref<Payload | null>(null)
  /** Host checks: audio, Bluetooth, D-Bus, memory, config dir. */
  const preflight = ref<Payload | null>(null)
  const recovery = ref<Payload | null>(null)
  const guidance = ref<Payload | null>(null)
  const loading = ref(false)

  async function fetchDiagnostics() {
    loading.value = true
    try {
      ;[health.value, preflight.value] = await Promise.all([getDiagnostics(), getPreflight()])
    } finally {
      loading.value = false
    }
  }

  async function fetchRecovery() {
    recovery.value = await getRecoveryAssistant()
  }

  async function fetchGuidance() {
    guidance.value = await getOperatorGuidance()
  }

  function downloadBugreport() {
    apiDownloadBugreport()
  }

  return { health, preflight, recovery, guidance, loading, fetchDiagnostics, fetchRecovery, fetchGuidance, downloadBugreport }
})
