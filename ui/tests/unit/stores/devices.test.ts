import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBridgeStore } from '@/stores/bridge'
import { deviceState, useDeviceStore } from '@/stores/devices'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/api/devices', () => ({
  reconnectDevice: vi.fn().mockResolvedValue({ id: 'job-1', status: 'succeeded' }),
  setDeviceEnabled: vi.fn().mockResolvedValue({ enabled: false, restart_required: true }),
  setManagement: vi.fn().mockResolvedValue(undefined),
  wakeDevice: vi.fn().mockResolvedValue(undefined),
  standbyDevice: vi.fn().mockResolvedValue(undefined),
  removeDevice: vi.fn().mockResolvedValue({ removed: true, reconfig: {} }),
}))
vi.mock('@/api/bluetooth', () => ({ forgetBtDevice: vi.fn().mockResolvedValue(undefined), getAdapters: vi.fn() }))
vi.mock('@/api/playback', () => ({
  setVolume: vi.fn().mockResolvedValue({ volume: 75 }),
  setMute: vi.fn().mockResolvedValue({ muted: true }),
}))

import { reconnectDevice, setDeviceEnabled, setManagement, standbyDevice, wakeDevice } from '@/api/devices'
import { removeDevice } from '@/api/devices'
import { setMute as apiSetMute, setVolume as apiSetVolume } from '@/api/playback'

const A = () =>
  makeDevice({ id: 'a', name: 'Alpha', bluetooth: { mac: 'AA:00:00:00:00:01', adapter: { hci: 'hci0' } } })
const B = () =>
  makeDevice({
    id: 'b',
    name: 'Bravo',
    bluetooth: { mac: 'AA:00:00:00:00:02', connected: false, adapter: { hci: 'hci1' } },
    health: { state: 'offline' },
    playback: { group: { id: 'g1' } },
  })

function setup() {
  const bridge = useBridgeStore()
  bridge.snapshot = makeStatus([A(), B()])
  return { bridge, store: useDeviceStore() }
}

describe('deviceState', () => {
  it('derives a short state from health, standby and enablement', () => {
    expect(deviceState(makeDevice())).toBe('ready')
    expect(deviceState(makeDevice({ enabled: false }))).toBe('disabled')
    expect(deviceState(makeDevice({ bluetooth: { standby: true } }))).toBe('standby')
    expect(deviceState(makeDevice({ bluetooth: { management_enabled: false, standby: true } }))).toBe('released')
    expect(deviceState(makeDevice({ playback: { playing: true }, audio: { streaming: true } }))).toBe('streaming')
    expect(deviceState(makeDevice({ health: { state: 'unknown' }, bluetooth: { connected: false } }))).toBe('offline')
  })
})

describe('deviceState: richer runtime states', () => {
  const connected = (patch: Record<string, unknown> = {}) => makeDevice(patch)

  it('says a speaker is stopping before anything but disabled and released', () => {
    expect(deviceState(connected({ sendspin: { stopping: true }, playback: { playing: true }, audio: { streaming: true } }))).toBe('stopping')
    expect(deviceState(connected({ sendspin: { stopping: true }, bluetooth: { management_enabled: false } }))).toBe('released')
  })

  it('says reconnecting for a Bluetooth or Music Assistant reconnect, ahead of standby', () => {
    expect(deviceState(connected({ bluetooth: { reconnecting: true, standby: true } }))).toBe('reconnecting')
    expect(deviceState(connected({ music_assistant: { reconnecting: true } }))).toBe('reconnecting')
  })

  it('says the sink is muted at system level when the player itself is not muted', () => {
    expect(deviceState(connected({ audio: { sink_muted: true, muted: false, has_sink: true } }))).toBe('sink_muted')
    expect(deviceState(connected({ audio: { sink_muted: true, muted: true } }))).not.toBe('sink_muted')
    expect(deviceState(connected({ audio: { sink_muted: true }, bluetooth: { connected: false } }))).not.toBe('sink_muted')
  })

  it('says buffering while audio is buffering or Music Assistant plays but the speaker has not started', () => {
    expect(deviceState(connected({ audio: { buffering: true } }))).toBe('buffering')
    expect(deviceState(connected({ music_assistant: { now_playing: { state: 'playing' } } }))).toBe('buffering')
    expect(deviceState(connected({ audio: { buffering: true, streaming: true }, playback: { playing: true } }))).toBe('streaming')
  })
})

describe('deviceState for a local audio output', () => {
  const pulse = (audio: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) =>
    makeDevice({
      backend: { type: 'pulse_sink', sink: 'alsa_output.usb', mac: null, adapter: null },
      bluetooth: null as never,
      audio: { has_sink: true, sink_missing: false, ...audio },
      ...extra,
    })

  it('is ready while its output is present, although it has no Bluetooth link', () => {
    expect(deviceState(pulse({}, { health: { state: 'unknown' } }))).toBe('ready')
  })

  it('is never offline for a Bluetooth reason while its output is present', () => {
    const health = { state: 'offline', summary: 'Bluetooth speaker disconnected', reasons: ['bluetooth_disconnected'] }
    expect(deviceState(pulse({}, { health }))).toBe('ready')
  })

  it('follows the bridge health for anything else', () => {
    expect(deviceState(pulse({}, { health: { state: 'degraded' } }))).toBe('degraded')
  })

  it('says the output is missing when the sink is gone', () => {
    expect(deviceState(pulse({ has_sink: false, sink_missing: true }))).toBe('output_missing')
  })

  it('is offline without a sink that is not reported missing', () => {
    expect(deviceState(pulse({ has_sink: false }, { health: { state: 'unknown' } }))).toBe('offline')
  })
})

describe('useDeviceStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('filters by name or MAC, state, adapter and group', () => {
    const { store } = setup()
    store.filter.search = 'bra'
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['b'])
    store.filter.search = 'aa:00:00:00:00:01'
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['a'])
    store.filter.search = ''
    store.filter.status = ['offline']
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['b'])
    store.filter.status = []
    store.filter.adapter = 'hci0'
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['a'])
    store.filter.adapter = ''
    store.filter.group = 'g1'
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['b'])
  })

  it('sorts by name both ways', () => {
    const { store } = setup()
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['a', 'b'])
    store.sortDir = 'desc'
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['b', 'a'])
  })

  it('selects by id', () => {
    const { store } = setup()
    store.selectDevice('b')
    expect(store.selectedDevice?.name).toBe('Bravo')
  })

  it('moves the volume at once and keeps it when the bridge agrees', async () => {
    const { store, bridge } = setup()
    await store.setVolume('a', 75)
    expect(apiSetVolume).toHaveBeenCalledWith('a', 75)
    expect(bridge.deviceById('a')!.audio.volume).toBe(75)
  })

  it('rolls the volume back when the bridge refuses', async () => {
    const { store, bridge } = setup()
    vi.mocked(apiSetVolume).mockRejectedValueOnce(new Error('nope'))
    await expect(store.setVolume('a', 10)).rejects.toThrow('nope')
    expect(bridge.deviceById('a')!.audio.volume).toBe(65)
  })

  it('applies the mute state the bridge reports', async () => {
    const { store, bridge } = setup()
    await store.setMute('a', true)
    expect(apiSetMute).toHaveBeenCalledWith('a', true)
    expect(bridge.deviceById('a')!.audio.muted).toBe(true)
  })

  it('sends commands by device id', async () => {
    const { store } = setup()
    await store.wake('a')
    await store.standby('a')
    await store.setEnabled('b', false)
    await store.release('a', true)
    expect(wakeDevice).toHaveBeenCalledWith('a')
    expect(standbyDevice).toHaveBeenCalledWith('a')
    expect(setDeviceEnabled).toHaveBeenCalledWith('b', false)
    expect(setManagement).toHaveBeenCalledWith('a', false)
  })

  it('follows reconnect to its finished job', async () => {
    const { store } = setup()
    const job = await store.reconnect('a')
    expect(reconnectDevice).toHaveBeenCalledWith('a')
    expect(job.status).toBe('succeeded')
  })

  it('removes a speaker from the bridge by id, then refreshes', async () => {
    const { store, bridge } = setup()
    const refresh = vi.spyOn(bridge, 'refresh').mockResolvedValue(undefined)
    await store.remove('b')
    expect(removeDevice).toHaveBeenCalledWith('b')
    expect(refresh).toHaveBeenCalled()
  })
})

describe('device sorting and backend filter', () => {
  const streaming = (id: string, name: string, group: string | null = null) =>
    makeDevice({ id, name, playback: { playing: true, group: { id: group } }, audio: { streaming: true } })
  const idle = (id: string, name: string, group: string | null = null) =>
    makeDevice({ id, name, playback: { group: { id: group } } })
  const off = (id: string, name: string, patch: Record<string, unknown> = {}) =>
    makeDevice({ id, name, bluetooth: { connected: false }, health: { state: 'offline' }, ...patch })
  const output = (id: string, name: string) =>
    makeDevice({ id, name, backend: { type: 'pulse_sink', sink: 'alsa_output.x' }, bluetooth: null, audio: { has_sink: true } } as never)

  beforeEach(() => {
    setActivePinia(createPinia())
    try {
      localStorage.clear()
    } catch {
      /* no storage */
    }
  })

  function withDevices(list: ReturnType<typeof makeDevice>[]) {
    const bridge = useBridgeStore()
    bridge.snapshot = makeStatus(list)
    return useDeviceStore()
  }

  it('sorts by activity by default: streaming, then connected, then the rest, each by name', () => {
    const store = withDevices([
      off('z', 'Zulu'),
      idle('c', 'Charlie'),
      streaming('s', 'Sierra'),
      makeDevice({ id: 'd', name: 'Delta', enabled: false }),
      idle('a', 'Alpha'),
      makeDevice({ id: 'r', name: 'Romeo', bluetooth: { management_enabled: false } }),
      makeDevice({ id: 'y', name: 'Yankee', bluetooth: { standby: true } }),
      streaming('b', 'Bravo'),
    ])
    expect(store.sortBy).toBe('activity')
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['b', 's', 'a', 'c', 'd', 'r', 'y', 'z'])
  })

  it('keeps members of one group together within a tier', () => {
    const store = withDevices([
      idle('k', 'Kilo', 'g2'),
      idle('a', 'Alpha'),
      idle('m', 'Mike', 'g1'),
      idle('b', 'Bravo', 'g1'),
      idle('z', 'Zulu', 'g2'),
      idle('c', 'Charlie'),
    ])
    // g1 sits where its first member (Bravo) would; g2 where Kilo would.
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['a', 'b', 'm', 'c', 'k', 'z'])
  })

  it('does not pull a group member out of its tier', () => {
    const store = withDevices([idle('m', 'Mike', 'g1'), streaming('z', 'Zulu', 'g1'), idle('a', 'Alpha')])
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['z', 'a', 'm'])
  })

  it('sorts by name or by status on request', () => {
    const store = withDevices([off('a', 'Alpha'), streaming('z', 'Zulu'), idle('m', 'Mike')])
    store.setSort('name')
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['a', 'm', 'z'])
    store.setSort('status')
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['z', 'm', 'a'])
  })

  it('keeps a speaker that plays in the top activity tier although its sink is muted at system level', () => {
    const store = withDevices([
      idle('a', 'Alpha'),
      makeDevice({ id: 'm', name: 'Mike', playback: { playing: true }, audio: { streaming: true, sink_muted: true, muted: false } }),
    ])
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['m', 'a'])
  })

  it('ranks a state it does not know just before offline in the status sort', () => {
    const store = withDevices([off('a', 'Alpha'), makeDevice({ id: 'w', name: 'Whiskey', health: { state: 'weird' } })])
    store.setSort('status')
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['w', 'a'])
  })

  it('remembers the chosen order across reloads', () => {
    withDevices([idle('a', 'Alpha')]).setSort('name')
    setActivePinia(createPinia())
    expect(useDeviceStore().sortBy).toBe('name')
  })

  it('falls back to activity when the remembered order is unknown or storage fails', () => {
    localStorage.setItem('sb-device-sort', 'bogus')
    expect(useDeviceStore().sortBy).toBe('activity')
    setActivePinia(createPinia())
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const store = useDeviceStore()
    expect(store.sortBy).toBe('activity')
    expect(() => store.setSort('name')).not.toThrow()
    expect(store.sortBy).toBe('name')
    spy.mockRestore()
    set.mockRestore()
  })

  it('filters by backend', () => {
    const store = withDevices([idle('a', 'Alpha'), output('o', 'Output')])
    store.filter.backend = 'output'
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['o'])
    store.filter.backend = 'bluetooth'
    expect(store.filteredDevices.map((d) => d.id)).toEqual(['a'])
    store.filter.backend = ''
    expect(store.filteredDevices).toHaveLength(2)
  })
})
