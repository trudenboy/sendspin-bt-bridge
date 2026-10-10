import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  discoverMA,
  getConnection,
  getGroups,
  getNowPlaying as apiGetNowPlaying,
  queueCommand,
  refreshGroups,
  signInWithPassword,
  silentAuth as apiSilentAuth,
} from '@/api/ma'
import { useJobsStore } from './jobs'
import type { MaGroup, NowPlaying, QueueAction } from '@/api/types'

export interface MaServer {
  url: string
  version?: string
  discovery_source?: string
  discovery_summary?: string
  [key: string]: unknown
}

export const useMaStore = defineStore('ma', () => {
  const connected = ref(false)
  const connection = ref<Record<string, unknown> | null>(null)
  const groups = ref<MaGroup[]>([])
  const nowPlaying = ref<NowPlaying>({})
  const servers = ref<MaServer[]>([])
  const discovering = ref(false)

  async function fetchConnection() {
    connection.value = await getConnection()
    connected.value = Boolean(connection.value?.connected)
  }

  async function fetchGroups() {
    groups.value = (await getGroups()) as MaGroup[]
  }

  async function refresh() {
    await useJobsStore().waitFor(await refreshGroups())
    await fetchGroups()
  }

  async function discover() {
    discovering.value = true
    try {
      const finished = await useJobsStore().waitFor(await discoverMA())
      const result = (finished.result ?? {}) as { servers?: MaServer[] }
      servers.value = result.servers ?? []
      return servers.value
    } finally {
      discovering.value = false
    }
  }

  async function getNowPlaying() {
    nowPlaying.value = (await apiGetNowPlaying()) as NowPlaying
    return nowPlaying.value
  }

  /** Now playing per Music Assistant sync group: each group card shows its own. */
  const nowPlayingByGroup = ref<Record<string, NowPlaying>>({})

  async function fetchGroupNowPlaying(syncgroupId: string) {
    const np = (await apiGetNowPlaying(syncgroupId)) as NowPlaying
    nowPlayingByGroup.value = { ...nowPlayingByGroup.value, [syncgroupId]: np }
    return np
  }

  async function queueCmd(
    action: QueueAction,
    target: { device_id?: string; syncgroup_id?: string; group_id?: string },
    value?: unknown,
  ) {
    const accepted = await queueCommand(action, target, value)
    if (accepted.ma_now_playing) {
      nowPlaying.value = accepted.ma_now_playing as NowPlaying
      if (target.syncgroup_id) {
        nowPlayingByGroup.value = { ...nowPlayingByGroup.value, [target.syncgroup_id]: accepted.ma_now_playing as NowPlaying }
      }
    }
    return useJobsStore().waitFor(accepted.job)
  }

  async function login(url: string, username: string, password: string) {
    const result = await signInWithPassword(url, username, password)
    connected.value = true
    return result
  }

  async function silentAuth(haToken: string, maUrl: string) {
    const result = await apiSilentAuth(haToken, maUrl)
    connected.value = true
    return result
  }

  return {
    connected,
    connection,
    groups,
    nowPlaying,
    servers,
    discovering,
    fetchConnection,
    fetchGroups,
    refresh,
    discover,
    getNowPlaying,
    queueCmd,
    nowPlayingByGroup,
    fetchGroupNowPlaying,
    login,
    silentAuth,
  }
})
