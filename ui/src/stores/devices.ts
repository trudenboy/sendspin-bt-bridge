import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useBridgeStore } from './bridge'
import {
  reconnectDevice,
  removeDevice,
  setDeviceEnabled,
  setManagement,
  setPowerSave as apiSetPowerSave,
  standbyDevice,
  wakeDevice,
} from '@/api/devices'
import { setMute as apiSetMute, setVolume as apiSetVolume } from '@/api/playback'
import { useJobsStore } from './jobs'
import { isDeviceConnected, isOutputMissing, isPulseSink } from '@/utils/deviceBackend'
import type { Device } from '@/api/types'

export type DeviceSortField = 'name' | 'status' | 'adapter'
export type SortDirection = 'asc' | 'desc'

export interface DeviceFilter {
  search: string
  status: string[]
  adapter: string
  group: string
}

/**
 * One status per speaker, most decisive first (stable 2.76's order): switched
 * off, handed over to other devices, stopping, reconnecting, parked on
 * standby, output gone, muted at system level, playing, starting to play,
 * then its health.
 */
export function deviceState(d: Device): string {
  if (!d.enabled) return 'disabled'
  if (d.bluetooth?.management_enabled === false) return 'released'
  if (d.sendspin.stopping) return 'stopping'
  if (d.bluetooth?.reconnecting || d.music_assistant.reconnecting) return 'reconnecting'
  if (d.bluetooth?.standby) return 'standby'
  if (isOutputMissing(d)) return 'output_missing'
  const connected = isDeviceConnected(d)
  if (connected && d.audio.has_sink && d.audio.sink_muted && !d.audio.muted) return 'sink_muted'
  if (d.playback.playing && d.audio.streaming) return 'streaming'
  if (connected && isBuffering(d)) return 'buffering'
  // A local output has no Bluetooth link: present means connected, whatever an
  // older bridge's Bluetooth-only health said.
  if (isPulseSink(d) && d.health.state === 'offline') return connected ? 'ready' : 'offline'
  if (d.health.state && d.health.state !== 'unknown') return d.health.state
  return connected ? 'ready' : 'offline'
}

/** Audio is buffering, or Music Assistant plays to the speaker but it has not started yet. */
function isBuffering(d: Device): boolean {
  if (d.audio.buffering) return true
  const np = d.music_assistant.now_playing as { state?: unknown } | null
  return np?.state === 'playing' && d.playback.server_connected && d.audio.has_sink && !d.playback.playing
}

export const useDeviceStore = defineStore('devices', () => {
  const selectedDeviceId = ref<string | null>(null)
  const filter = ref<DeviceFilter>({ search: '', status: [], adapter: '', group: '' })
  const sortBy = ref<DeviceSortField>('name')
  const sortDir = ref<SortDirection>('asc')

  const bridge = useBridgeStore()

  const filteredDevices = computed<Device[]>(() => {
    let list = [...bridge.devices]
    const q = filter.value.search.toLowerCase()
    if (q) {
      list = list.filter(
        (d) => (d.name ?? '').toLowerCase().includes(q) || (d.bluetooth?.mac ?? '').toLowerCase().includes(q),
      )
    }
    if (filter.value.status.length) list = list.filter((d) => filter.value.status.includes(deviceState(d)))
    if (filter.value.adapter) list = list.filter((d) => d.bluetooth?.adapter.hci === filter.value.adapter)
    if (filter.value.group) list = list.filter((d) => d.playback.group.id === filter.value.group)

    list.sort((a, b) => {
      let cmp = 0
      if (sortBy.value === 'name') cmp = (a.name ?? '').localeCompare(b.name ?? '')
      else if (sortBy.value === 'status') cmp = deviceState(a).localeCompare(deviceState(b))
      else cmp = (a.bluetooth?.adapter.hci ?? '').localeCompare(b.bluetooth?.adapter.hci ?? '')
      return sortDir.value === 'asc' ? cmp : -cmp
    })
    return list
  })

  const selectedDevice = computed<Device | undefined>(() =>
    selectedDeviceId.value ? bridge.deviceById(selectedDeviceId.value) : undefined,
  )

  function selectDevice(id: string | null) {
    selectedDeviceId.value = id
  }

  /** Optimistic: the slider moves at once and rolls back if the bridge refuses. */
  async function setVolume(id: string, level: number) {
    const device = bridge.deviceById(id)
    const previous = device?.audio.volume
    if (device) device.audio.volume = level
    try {
      await apiSetVolume(id, level)
    } catch (e) {
      if (device && previous !== undefined) device.audio.volume = previous
      throw e
    }
  }

  async function setMute(id: string, muted?: boolean) {
    const { muted: actual } = await apiSetMute(id, muted)
    const device = bridge.deviceById(id)
    if (device) device.audio.muted = actual
  }

  async function reconnect(id: string) {
    return useJobsStore().waitFor(await reconnectDevice(id))
  }

  async function wake(id: string) {
    await wakeDevice(id)
  }

  async function standby(id: string) {
    await standbyDevice(id)
  }

  /** Suspend (true) or resume the speaker's audio output; the Bluetooth link stays up. */
  async function setPowerSave(id: string, enabled: boolean) {
    await apiSetPowerSave(id, enabled)
  }

  async function release(id: string, released: boolean) {
    await setManagement(id, !released)
  }

  /** Take the speaker off the bridge (player and Bluetooth pairing go with it). */
  async function remove(id: string) {
    await removeDevice(id)
    await bridge.refresh()
  }

  async function setEnabled(id: string, enabled: boolean) {
    return setDeviceEnabled(id, enabled)
  }

  return {
    selectedDeviceId,
    filter,
    sortBy,
    sortDir,
    filteredDevices,
    selectedDevice,
    selectDevice,
    setVolume,
    setMute,
    reconnect,
    wake,
    standby,
    setPowerSave,
    release,
    remove,
    setEnabled,
  }
})
