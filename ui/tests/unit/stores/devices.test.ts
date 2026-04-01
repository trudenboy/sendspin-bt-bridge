import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import type { BridgeSnapshot } from '@/api/types'

vi.mock('@/api/devices', () => ({
  reconnectDevice: vi.fn().mockResolvedValue({ success: true }),
  removeDevice: vi.fn().mockResolvedValue({ success: true }),
  setDeviceEnabled: vi.fn().mockResolvedValue({ success: true }),
  wakeDevice: vi.fn().mockResolvedValue({ success: true }),
  standbyDevice: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/api/playback', () => ({
  setVolume: vi.fn().mockResolvedValue({ success: true, volume: 75 }),
  setMute: vi.fn().mockResolvedValue({ success: true, muted: true }),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import { reconnectDevice, removeDevice, setDeviceEnabled, wakeDevice, standbyDevice } from '@/api/devices'
import { setVolume as apiSetVolume, setMute as apiSetMute } from '@/api/playback'

const DEVICE_A = {
  player_name: 'Speaker A',
  mac: 'AA:BB:CC:DD:EE:01',
  enabled: true,
  status: 'connected',
  connected: true,
  audio_streaming: false,
  server_connected: true,
  volume: 50,
  muted: false,
  player_state: 'READY' as const,
  backend_info: { type: 'bluetooth_a2dp' as const, mac: 'AA:BB:CC:DD:EE:01', capabilities: [] },
}

const DEVICE_B = {
  player_name: 'Speaker B',
  mac: 'AA:BB:CC:DD:EE:02',
  enabled: true,
  status: 'disconnected',
  connected: false,
  audio_streaming: false,
  server_connected: false,
  volume: 30,
  muted: true,
  player_state: 'OFFLINE' as const,
  backend_info: { type: 'local_sink' as const, capabilities: [] },
}

function makeSnapshot(): BridgeSnapshot {
  return {
    version: '2.50.0',
    build_date: '2025-07-18',
    uptime_seconds: 1000,
    devices: [{ ...DEVICE_A }, { ...DEVICE_B }],
    groups: [],
    adapters: [],
    ma_connected: false,
  }
}

describe('useDeviceStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('has correct initial state', () => {
    const store = useDeviceStore()
    expect(store.selectedDeviceId).toBeNull()
    expect(store.filter.search).toBe('')
    expect(store.sortBy).toBe('name')
    expect(store.sortDir).toBe('asc')
  })

  it('filteredDevices returns all devices with no filter', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    expect(store.filteredDevices).toHaveLength(2)
  })

  it('filteredDevices filters by search string', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    store.filter.search = 'Speaker A'
    expect(store.filteredDevices).toHaveLength(1)
    expect(store.filteredDevices[0].mac).toBe('AA:BB:CC:DD:EE:01')
  })

  it('filteredDevices filters by MAC address', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    store.filter.search = 'EE:02'
    expect(store.filteredDevices).toHaveLength(1)
    expect(store.filteredDevices[0].player_name).toBe('Speaker B')
  })

  it('filteredDevices filters by status', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    store.filter.status = ['OFFLINE']
    expect(store.filteredDevices).toHaveLength(1)
    expect(store.filteredDevices[0].mac).toBe('AA:BB:CC:DD:EE:02')
  })

  it('filteredDevices filters by backend type', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    store.filter.backendType = ['bluetooth_a2dp']
    expect(store.filteredDevices).toHaveLength(1)
    expect(store.filteredDevices[0].mac).toBe('AA:BB:CC:DD:EE:01')
  })

  it('filteredDevices sorts by name ascending', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    store.sortBy = 'name'
    store.sortDir = 'asc'
    expect(store.filteredDevices[0].player_name).toBe('Speaker A')
    expect(store.filteredDevices[1].player_name).toBe('Speaker B')
  })

  it('filteredDevices sorts descending', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    store.sortBy = 'name'
    store.sortDir = 'desc'
    expect(store.filteredDevices[0].player_name).toBe('Speaker B')
  })

  it('selectedDevice returns correct device', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    store.selectDevice('AA:BB:CC:DD:EE:01')
    expect(store.selectedDevice?.player_name).toBe('Speaker A')
  })

  it('selectedDevice returns undefined when no selection', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    expect(store.selectedDevice).toBeUndefined()
  })

  it('devicesByBackend groups devices correctly', () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    expect(store.devicesByBackend['bluetooth_a2dp']).toHaveLength(1)
    expect(store.devicesByBackend['local_sink']).toHaveLength(1)
  })

  it('setVolume optimistically updates then calls API', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    await store.setVolume('AA:BB:CC:DD:EE:01', 75)
    expect(bridge.snapshot!.devices[0].volume).toBe(75)
    expect(apiSetVolume).toHaveBeenCalledWith('Speaker A', 75)
  })

  it('setVolume rolls back on API failure', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    vi.mocked(apiSetVolume).mockRejectedValueOnce(new Error('fail'))
    const store = useDeviceStore()
    await expect(store.setVolume('AA:BB:CC:DD:EE:01', 75)).rejects.toThrow('fail')
    expect(bridge.snapshot!.devices[0].volume).toBe(50)
  })

  it('setMute calls API with player name', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    await store.setMute('AA:BB:CC:DD:EE:01', true)
    expect(apiSetMute).toHaveBeenCalledWith('Speaker A', true)
  })

  it('reconnect calls API', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    await store.reconnect('AA:BB:CC:DD:EE:01')
    expect(reconnectDevice).toHaveBeenCalledWith('AA:BB:CC:DD:EE:01')
  })

  it('wake calls API', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    await store.wake('AA:BB:CC:DD:EE:01')
    expect(wakeDevice).toHaveBeenCalledWith('AA:BB:CC:DD:EE:01')
  })

  it('standby calls API', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    await store.standby('AA:BB:CC:DD:EE:01')
    expect(standbyDevice).toHaveBeenCalledWith('AA:BB:CC:DD:EE:01')
  })

  it('remove calls API', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    await store.remove('AA:BB:CC:DD:EE:01')
    expect(removeDevice).toHaveBeenCalledWith('AA:BB:CC:DD:EE:01')
  })

  it('setEnabled calls API with player name', async () => {
    const bridge = useBridgeStore()
    bridge.snapshot = makeSnapshot()
    const store = useDeviceStore()
    await store.setEnabled('AA:BB:CC:DD:EE:01', false)
    expect(setDeviceEnabled).toHaveBeenCalledWith('Speaker A', false)
  })
})
