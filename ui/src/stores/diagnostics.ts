import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getDiagnostics,
  getRecoveryAssistant,
  getOperatorGuidance,
  downloadBugreport as apiDownloadBugreport,
} from '@/api/diagnostics'
import type {
  DiagnosticsData,
  RecoveryData,
  OperatorGuidance,
} from '@/api/types'

export const useDiagnosticsStore = defineStore('diagnostics', () => {
  const health = ref<DiagnosticsData | null>(null)
  const recovery = ref<RecoveryData | null>(null)
  const guidance = ref<OperatorGuidance | null>(null)
  const loading = ref(false)

  async function fetchDiagnostics() {
    loading.value = true
    try {
      health.value = await getDiagnostics()
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

  return {
    health,
    recovery,
    guidance,
    loading,
    fetchDiagnostics,
    fetchRecovery,
    fetchGuidance,
    downloadBugreport,
  }
})
