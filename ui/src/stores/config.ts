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
    try {
      const data = await getConfig()
      config.value = deepClone(data)
      originalConfig.value = deepClone(data)
      _originalJson.value = JSON.stringify(data)
      validationErrors.value = {}
    } finally {
      loading.value = false
    }
  }

  async function saveConfig() {
    if (!config.value) return
    saving.value = true
    try {
      await apiSaveConfig(config.value)
      const snapshot = JSON.stringify(config.value)
      originalConfig.value = JSON.parse(snapshot)
      _originalJson.value = snapshot
      validationErrors.value = {}
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
    if (!result.valid && result.errors) {
      const errors: Record<string, string> = {}
      result.errors.forEach((err, i) => {
        errors[String(i)] = err
      })
      validationErrors.value = errors
    } else {
      validationErrors.value = {}
    }
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
