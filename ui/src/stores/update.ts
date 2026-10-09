import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { applyUpdate, getUpdateInfo, startUpdateCheck, type UpdateChannel, type UpdateInfo } from '@/api/updates'
import { useJobsStore } from './jobs'
import { useNotificationStore } from './notifications'

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

  /** Ask GitHub now; opens the dialog when a newer release exists. */
  async function checkForUpdates(requestedChannel?: UpdateChannel) {
    checking.value = true
    error.value = null
    const notifications = useNotificationStore()
    try {
      const finished = await useJobsStore().waitFor(await startUpdateCheck(requestedChannel))
      if (finished.status !== 'succeeded') {
        error.value = finished.error?.detail ?? 'Update check failed'
        notifications.error(error.value)
        return
      }
      await fetchInfo()
      if (info.value?.update_available) showDialog.value = true
      else if (!showDialog.value) notifications.info('update.upToDate')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Update check failed'
      notifications.error(error.value)
    } finally {
      checking.value = false
    }
  }

  /** Install the newer release (LXC / bare metal; the add-on and Docker update elsewhere). */
  async function doApplyUpdate() {
    applying.value = true
    error.value = null
    const notifications = useNotificationStore()
    try {
      const result = await applyUpdate(info.value?.tag, info.value?.channel as UpdateChannel | undefined)
      notifications.success('update.applyStarted')
      showDialog.value = false
      return result
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Update failed'
      notifications.error(error.value)
      return null
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
