import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  getUpdateInfo,
  startUpdateCheck,
  getUpdateCheckResult,
  applyUpdate,
  type UpdateInfo,
} from '@/api/updates'
import { saveConfig } from '@/api/config'
import { useNotificationStore } from './notifications'

const POLL_INTERVAL = 1500
const MAX_POLL_ATTEMPTS = 40

export const useUpdateStore = defineStore('update', () => {
  const info = ref<UpdateInfo | null>(null)
  const checking = ref(false)
  const applying = ref(false)
  const loading = ref(false)
  const showDialog = ref(false)
  const error = ref<string | null>(null)

  const updateAvailable = computed(() => info.value?.update_available ?? false)
  const latestVersion = computed(() => info.value?.version ?? null)
  const releaseNotes = computed(() => info.value?.body ?? null)
  const runtime = computed(() => info.value?.runtime ?? 'unknown')
  const updateMethod = computed(() => info.value?.update_method ?? 'manual')
  const channel = computed(() => info.value?.channel ?? 'stable')

  /** Fetch cached update info from backend. */
  async function fetchInfo() {
    loading.value = true
    error.value = null
    try {
      info.value = await getUpdateInfo()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch update info'
    } finally {
      loading.value = false
    }
  }

  /** Start a fresh update check and poll until complete. */
  async function checkForUpdates(requestedChannel?: string) {
    checking.value = true
    error.value = null
    const notifications = useNotificationStore()
    try {
      const job = await startUpdateCheck(requestedChannel)
      const jobId = job.job_id

      let attempts = 0
      while (attempts < MAX_POLL_ATTEMPTS) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL))
        const result = await getUpdateCheckResult(jobId)
        if (result.status === 'completed') {
          await fetchInfo()
          if (info.value?.update_available) {
            showDialog.value = true
          } else if (!showDialog.value) {
            notifications.info('update.upToDate')
          }
          return
        }
        if (result.status === 'failed') {
          error.value = result.error ?? 'Update check failed'
          notifications.error(error.value)
          return
        }
        attempts++
      }
      error.value = 'Update check timed out'
      notifications.warning(error.value)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Update check failed'
      notifications.error(error.value)
    } finally {
      checking.value = false
    }
  }

  /** Apply update (systemd/LXC only). Persists channel if changed. */
  async function doApplyUpdate() {
    applying.value = true
    error.value = null
    const notifications = useNotificationStore()
    try {
      const targetChannel = info.value?.channel
      const result = await applyUpdate(info.value?.tag, targetChannel)
      if (result.success) {
        // Persist channel choice so future checks use this channel
        if (targetChannel) {
          saveConfig({ UPDATE_CHANNEL: targetChannel } as any).catch(() => {})
        }
        notifications.success('update.applyStarted')
        showDialog.value = false
      } else {
        error.value = result.error ?? 'Update failed'
        notifications.error(error.value)
      }
      return result
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Update failed'
      notifications.error(error.value)
      return { success: false, error: error.value }
    } finally {
      applying.value = false
    }
  }

  function openDialog() {
    showDialog.value = true
  }

  return {
    info,
    checking,
    applying,
    loading,
    showDialog,
    error,
    updateAvailable,
    latestVersion,
    releaseNotes,
    runtime,
    updateMethod,
    channel,
    fetchInfo,
    checkForUpdates,
    doApplyUpdate,
    openDialog,
  }
})
