import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getGroups,
  getNowPlaying as apiGetNowPlaying,
  discoverMA,
  queueCmd as apiQueueCmd,
  maLogin as apiMaLogin,
  silentAuth as apiSilentAuth,
} from '@/api/ma'
import type { SyncGroup, NowPlaying } from '@/api/types'

export const useMaStore = defineStore('ma', () => {
  const connected = ref(false)
  const groups = ref<SyncGroup[]>([])
  const nowPlaying = ref<Record<string, NowPlaying>>({})
  const discovering = ref(false)

  async function fetchGroups() {
    groups.value = await getGroups()
  }

  async function discover() {
    discovering.value = true
    try {
      await discoverMA()
    } finally {
      discovering.value = false
    }
  }

  async function getNowPlaying(groupId?: string) {
    const data = await apiGetNowPlaying()
    nowPlaying.value = data
    if (groupId) return data[groupId] ?? null
    return data
  }

  async function queueCmd(
    action: string,
    groupId?: string,
    params?: Record<string, unknown>,
  ) {
    await apiQueueCmd(action, groupId, params)
  }

  async function login(haToken: string) {
    const result = await apiMaLogin(haToken)
    if (result.success) {
      connected.value = true
    }
    return result
  }

  async function silentAuth(haToken: string, maUrl: string) {
    const result = await apiSilentAuth(haToken, maUrl)
    if (result.success) {
      connected.value = true
    }
    return result
  }

  return {
    connected,
    groups,
    nowPlaying,
    discovering,
    fetchGroups,
    discover,
    getNowPlaying,
    queueCmd,
    login,
    silentAuth,
  }
})
