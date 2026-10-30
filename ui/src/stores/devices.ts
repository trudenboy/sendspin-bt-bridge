import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useBridgeStore } from './bridge'
import {
  reconnectDevice,
  removeDevice,
  setDeviceEnabled,
  setDevicePlayoutSync,
  setManagement,
  setPowerSave as apiSetPowerSave,
  standbyDevice,
  wakeDevice,
} from '@/api/devices'
import { setMute as apiSetMute, setVolume as apiSetVolume } from '@/api/playback'
import { useJobsStore } from './jobs'
import { isDeviceConnected, isOutputMissing, isPulseSink, resolveAdapter } from '@/utils/deviceBackend'
import type { Device, DisabledDevice } from '@/api/types'

export type DeviceSortField = 'activity' | 'name' | 'status' | 'adapter'
export type SortDirection = 'asc' | 'desc'
/** Which kind of player: a Bluetooth speaker or a local audio output ('' = both). */
export type BackendFilter = '' | 'bluetooth' | 'output'

export interface DeviceFilter {
  search: string
  status: string[]
  adapter: string
  group: string
  backend: BackendFilter
}

/** A configured player the bridge does not run: shown like the others, dimmed. */
export interface DisabledPlayer {
  id: string
  name: string
  mac: string | null
  /** Bluetooth adapter it is set to (hci name or MAC). */
  adapter: string | null
  /** A local audio output rather than a Bluetooth speaker. */
  output: boolean
  sink: string | null
  room: string | null
}

/** One entry of the dashboard: a running player or a disabled one. */
export type PlayerEntry =
  | { kind: 'device'; id: string; name: string; device: Device }
  | { kind: 'disabled'; id: string; name: string; player: DisabledPlayer }

const str = (v: unknown) => (typeof v === 'string' && v ? v : null)

/** The bridge's disabled list as players; entries without an id cannot be acted on and are left out. */
export function toDisabledPlayers(list: DisabledDevice[] | null | undefined): DisabledPlayer[] {
  return (list ?? [])
    .filter((d) => !!d.id)
    .map((d) => ({
      id: d.id!,
      name: d.player_name ?? d.mac ?? d.id!,
      mac: d.mac ?? null,
      adapter: str(d.adapter),
      output: d.backend_type === 'pulse_sink',
      sink: str(d.sink),
      room: str(d.room_name),
    }))
}

export const SORT_OPTIONS: DeviceSortField[] = ['activity', 'name', 'status']
const SORT_STORAGE_KEY = 'sb-device-sort'

/* Storage can be missing or throw (private windows, blocked site data). */
function readSort(): DeviceSortField {
  try {
    const saved = localStorage.getItem(SORT_STORAGE_KEY) as DeviceSortField | null
    if (saved && (SORT_OPTIONS as string[]).includes(saved)) return saved
  } catch {
    /* fall through */
  }
  return 'activity'
}

function writeSort(value: DeviceSortField) {
  try {
    localStorage.setItem(SORT_STORAGE_KEY, value)
  } catch {
    /* the choice just isn't remembered */
  }
}

/** States that are neither playing nor merely idle: parked, handed over, gone, off. */
const RESTING = new Set(['disabled', 'released', 'standby', 'output_missing', 'offline', 'stopping'])

/** 0 playing (or about to), 1 connected and idle, 2 everything else. */
export function activityTier(d: Device): 0 | 1 | 2 {
  const state = deviceState(d)
  // A speaker that plays is active even when its sink is muted at system level.
  if (state === 'streaming' || state === 'buffering' || (d.playback.playing && d.audio.streaming)) return 0
  if (!RESTING.has(state) && isDeviceConnected(d)) return 1
  return 2
}

/** Status order for the "status" sort: most active first, switched off last. */
const STATUS_RANK = [
  'streaming',
  'buffering',
  'sink_muted',
  'ready',
  'degraded',
  'reconnecting',
  'stopping',
  'standby',
  'released',
  'output_missing',
  'offline',
  'disabled',
]
/** A state this list does not know (a newer bridge's) sorts just before offline. */
const statusRank = (d: Device) => {
  const i = STATUS_RANK.indexOf(deviceState(d))
  return i < 0 ? STATUS_RANK.indexOf('offline') - 0.5 : i
}

const byName = (a: Device, b: Device) => (a.name ?? '').localeCompare(b.name ?? '')
const groupKey = (d: Device) => d.music_assistant.syncgroup_id || d.playback.group.id || ''

/**
 * Activity order (stable 2.76's dashboard): playing first, then connected,
 * then the rest; within a tier, members of one Music Assistant group sit
 * together where the group's first name would, otherwise by name.
 */
function sortByActivity(list: Device[]): Device[] {
  const tier = new Map(list.map((d) => [d.id, activityTier(d)]))
  const anchor = new Map<string, string>()
  for (const d of list) {
    const g = groupKey(d)
    if (!g) continue
    const key = `${tier.get(d.id)}|${g}`
    const name = d.name ?? ''
    const current = anchor.get(key)
    if (current === undefined || name.localeCompare(current) < 0) anchor.set(key, name)
  }
  const anchorOf = (d: Device) => {
    const g = groupKey(d)
    return g ? anchor.get(`${tier.get(d.id)}|${g}`)! : (d.name ?? '')
  }
  return [...list].sort(
    (a, b) =>
      tier.get(a.id)! - tier.get(b.id)! ||
      anchorOf(a).localeCompare(anchorOf(b)) ||
      groupKey(a).localeCompare(groupKey(b)) ||
      byName(a, b),
  )
}

/** The devices in the requested order. */
export function sortDevices(list: Device[], by: DeviceSortField, dir: SortDirection = 'asc'): Device[] {
  let sorted: Device[]
  if (by === 'activity') sorted = sortByActivity(list)
  else if (by === 'status') sorted = [...list].sort((a, b) => statusRank(a) - statusRank(b) || byName(a, b))
  else if (by === 'adapter')
    sorted = [...list].sort(
      (a, b) => (a.bluetooth?.adapter.hci ?? '').localeCompare(b.bluetooth?.adapter.hci ?? '') || byName(a, b),
    )
  else sorted = [...list].sort(byName)
  return dir === 'asc' ? sorted : sorted.reverse()
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
  const filter = ref<DeviceFilter>({ search: '', status: [], adapter: '', group: '', backend: '' })
  function clearFilters() {
    filter.value = { search: '', status: [], adapter: '', group: '', backend: '' }
  }
  const sortBy = ref<DeviceSortField>(readSort())
  const sortDir = ref<SortDirection>('asc')

  const bridge = useBridgeStore()

  const disabledPlayers = computed<DisabledPlayer[]>(() => toDisabledPlayers(bridge.bridge?.disabled_devices))

  /**
   * Disabled players through the same filters: status "disabled", no group, their
   * own backend, and their adapter resolved to its hci (the config may name it by MAC).
   */
  const filteredDisabled = computed<DisabledPlayer[]>(() => {
    const f = filter.value
    const q = f.search.toLowerCase()
    return disabledPlayers.value.filter(
      (p) =>
        (!q || p.name.toLowerCase().includes(q) || (p.mac ?? '').toLowerCase().includes(q)) &&
        (!f.status.length || f.status.includes('disabled')) &&
        (!f.adapter || (!!p.adapter && resolveAdapter(p.adapter, bridge.adapters ?? []).hci === f.adapter)) &&
        !f.group &&
        (!f.backend || p.output === (f.backend === 'output')),
    )
  })

  /**
   * Running and disabled players in one list: by name they mix; in activity
   * and status order disabled players come last (they are the least active).
   */
  const filteredPlayers = computed<PlayerEntry[]>(() => {
    const running: PlayerEntry[] = filteredDevices.value.map((d) => ({ kind: 'device', id: d.id, name: d.name ?? '', device: d }))
    const off: PlayerEntry[] = [...filteredDisabled.value]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((p) => ({ kind: 'disabled', id: p.id, name: p.name, player: p }))
    if (sortBy.value !== 'name') return [...running, ...off]
    const merged = [...running, ...off].sort((a, b) => a.name.localeCompare(b.name))
    return sortDir.value === 'asc' ? merged : merged.reverse()
  })

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
    if (filter.value.backend) list = list.filter((d) => isPulseSink(d) === (filter.value.backend === 'output'))
    return sortDevices(list, sortBy.value, sortDir.value)
  })

  function setSort(value: DeviceSortField) {
    sortBy.value = value
    writeSort(value)
  }

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

  async function setPlayoutSync(id: string, enabled: boolean) {
    return setDevicePlayoutSync(id, enabled)
  }

  return {
    selectedDeviceId,
    filter,
    clearFilters,
    sortBy,
    sortDir,
    setSort,
    filteredDevices,
    disabledPlayers,
    filteredPlayers,
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
    setPlayoutSync,
  }
})
