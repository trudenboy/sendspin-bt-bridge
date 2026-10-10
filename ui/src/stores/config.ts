import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  getConfig,
  saveConfig as apiSaveConfig,
  validateConfig as apiValidateConfig,
  downloadConfig as apiDownloadConfig,
  uploadConfig as apiUploadConfig,
} from '@/api/config'
import type { BridgeConfig } from '@/api/types'

function deepClone<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj))
}

export const useConfigStore = defineStore('config', () => {
  const config = ref<BridgeConfig | null>(null)
  const originalConfig = ref<BridgeConfig | null>(null)
  /* Serialized snapshot of original for dirty comparison —
     avoids reactive-proxy issues with structuredClone/JSON.stringify. */
  const _originalJson = ref('')
  const loading = ref(false)
  /** Why the last load failed (null when it worked). */
  const loadError = ref<string | null>(null)
  const saving = ref(false)
  const validationErrors = ref<Record<string, string>>({})

  const isDirty = computed(() => {
    if (!config.value || !_originalJson.value) return false
    return JSON.stringify(config.value) !== _originalJson.value
  })

  const isValid = computed(
    () => Object.keys(validationErrors.value).length === 0,
  )

  async function fetchConfig() {
    loading.value = true
    loadError.value = null
    try {
      const data = await getConfig()
      config.value = deepClone(data)
      originalConfig.value = deepClone(data)
      _originalJson.value = JSON.stringify(data)
      validationErrors.value = {}
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  /** Saves and applies; returns what the bridge applied live and what needs a restart. */
  async function saveConfig() {
    if (!config.value) return
    saving.value = true
    try {
      const result = await apiSaveConfig(config.value)
      const snapshot = JSON.stringify(config.value)
      originalConfig.value = JSON.parse(snapshot)
      _originalJson.value = snapshot
      validationErrors.value = {}
      return result
    } finally {
      saving.value = false
    }
  }

  function updateField(path: string, value: unknown) {
    if (!config.value) return
    const keys = path.split('.')
    const last = keys.pop()!
    let obj: Record<string, unknown> = config.value as Record<string, unknown>
    for (const key of keys) {
      if (typeof obj[key] !== 'object' || obj[key] === null) {
        obj[key] = {}
      }
      obj = obj[key] as Record<string, unknown>
    }
    obj[last] = value
  }

  function resetChanges() {
    if (_originalJson.value) {
      config.value = JSON.parse(_originalJson.value)
    }
    validationErrors.value = {}
  }

  async function validateConfig() {
    if (!config.value) return
    const result = await apiValidateConfig(config.value)
    const errors: Record<string, string> = {}
    for (const issue of result.errors) errors[issue.field] = issue.message
    validationErrors.value = errors
    return result
  }

  async function uploadConfig(file: File) {
    await apiUploadConfig(file)
    await fetchConfig()
  }

  function downloadConfig() {
    apiDownloadConfig()
  }

  return {
    config,
    originalConfig,
    loading,
    loadError,
    saving,
    validationErrors,
    isDirty,
    isValid,
    fetchConfig,
    saveConfig,
    updateField,
    resetChanges,
    validateConfig,
    uploadConfig,
    downloadConfig,
  }
})
