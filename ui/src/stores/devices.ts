import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useBridgeStore } from './bridge'
import {
  reconnectDevice,
  removeDevice,
  setDeviceEnabled,
  wakeDevice,
  standbyDevice,
} from '@/api/devices'
import { setVolume as apiSetVolume, setMute as apiSetMute } from '@/api/playback'
import type { DeviceSnapshot } from '@/api/types'

export type DeviceSortField = 'name' | 'status' | 'backend'
export type SortDirection = 'asc' | 'desc'

export interface DeviceFilter {
  search: string
  status: string[]
  backendType: string[]
  adapter: string
  group: string
}

export const useDeviceStore = defineStore('devices', () => {
  const selectedDeviceId = ref<string | null>(null)
  const filter = ref<DeviceFilter>({
    search: '',
    status: [],
    backendType: [],
    adapter: '',
    group: '',
  })
  const sortBy = ref<DeviceSortField>('name')
  const sortDir = ref<SortDirection>('asc')

  const bridge = useBridgeStore()

  function findName(mac: string): string | undefined {
    return bridge.devices.find((d) => d.mac === mac)?.player_name
  }

  /* Getters */

  const filteredDevices = computed<DeviceSnapshot[]>(() => {
    let list = [...bridge.devices]

    const q = filter.value.search.toLowerCase()
    if (q) {
      list = list.filter(
        (d) =>
          d.player_name.toLowerCase().includes(q) ||
          d.mac.toLowerCase().includes(q),
      )
    }
    if (filter.value.status.length) {
      list = list.filter((d) =>
        filter.value.status.includes(d.player_state ?? d.status),
      )
    }
    if (filter.value.backendType.length) {
      list = list.filter(
        (d) =>
          d.backend_info &&
          filter.value.backendType.includes(d.backend_info.type),
      )
    }
    if (filter.value.adapter) {
      list = list.filter((d) => d.adapter === filter.value.adapter)
    }
    if (filter.value.group) {
      const group = bridge.groups.find((g) => g.group_id === filter.value.group)
      if (group) {
        const memberNames = new Set(group.members.map((m) => m.player_name))
        list = list.filter((d) => memberNames.has(d.player_name))
      }
    }

    list.sort((a, b) => {
      let cmp = 0
      if (sortBy.value === 'name')
        cmp = a.player_name.localeCompare(b.player_name)
      else if (sortBy.value === 'status')
        cmp = (a.player_state ?? a.status).localeCompare(
          b.player_state ?? b.status,
        )
      else if (sortBy.value === 'backend')
        cmp = (a.backend_info?.type ?? '').localeCompare(
          b.backend_info?.type ?? '',
        )
      return sortDir.value === 'asc' ? cmp : -cmp
    })

    return list
  })

  const selectedDevice = computed<DeviceSnapshot | undefined>(() =>
    bridge.devices.find((d) => d.mac === selectedDeviceId.value),
  )

  const devicesByBackend = computed<Record<string, DeviceSnapshot[]>>(() => {
    const groups: Record<string, DeviceSnapshot[]> = {}
    for (const d of bridge.devices) {
      const key = d.backend_info?.type ?? 'unknown'
      ;(groups[key] ??= []).push(d)
    }
    return groups
  })

  /* Actions */

  function selectDevice(id: string | null) {
    selectedDeviceId.value = id
  }

  async function setVolume(mac: string, volume: number) {
    const device = bridge.snapshot?.devices.find((d) => d.mac === mac)
    const prev = device?.volume
    if (device) device.volume = volume

    const name = findName(mac)
    if (!name) return
    try {
      await apiSetVolume(name, volume)
    } catch (e) {
      if (device && prev !== undefined) device.volume = prev
      throw e
    }
  }

  async function setMute(mac: string, muted: boolean) {
    const name = findName(mac)
    if (!name) return
    await apiSetMute(name, muted)
  }

  async function reconnect(mac: string) {
    await reconnectDevice(mac)
  }

  async function wake(mac: string) {
    await wakeDevice(mac)
  }

  async function standby(mac: string) {
    await standbyDevice(mac)
  }

  async function remove(mac: string) {
    await removeDevice(mac)
  }

  async function setEnabled(mac: string, enabled: boolean) {
    const name = findName(mac)
    if (!name) return
    await setDeviceEnabled(name, enabled)
  }

  return {
    selectedDeviceId,
    filter,
    sortBy,
    sortDir,
    filteredDevices,
    selectedDevice,
    devicesByBackend,
    selectDevice,
    setVolume,
    setMute,
    reconnect,
    wake,
    standby,
    remove,
    setEnabled,
  }
})
