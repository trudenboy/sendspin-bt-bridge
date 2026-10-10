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
