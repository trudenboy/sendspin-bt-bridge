import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/devices', () => ({
  getAdapters: vi.fn(),
  startScan: vi.fn(),
  getScanResult: vi.fn(),
  pairDevice: vi.fn().mockResolvedValue({ success: true }),
  rebootAdapter: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import {
  getAdapters,
  startScan,
  getScanResult,
  pairDevice,
  rebootAdapter,
} from '@/api/devices'
import { useBluetoothStore } from '@/stores/bluetooth'

describe('useBluetoothStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('has correct initial state', () => {
    const store = useBluetoothStore()
    expect(store.adapters).toEqual([])
    expect(store.scanJobId).toBeNull()
    expect(store.scanResults).toEqual([])
    expect(store.scanning).toBe(false)
    expect(store.pairing).toBe(false)
    expect(store.pairTarget).toBeNull()
  })

  it('fetchAdapters loads adapter list', async () => {
    const mockAdapters = [
      { hci_device: 'hci0', name: 'Adapter 1', mac: 'AA:BB:CC:DD:EE:01', powered: true },
    ]
    vi.mocked(getAdapters).mockResolvedValue(mockAdapters)
    const store = useBluetoothStore()
    await store.fetchAdapters()
    expect(store.adapters).toEqual(mockAdapters)
  })

  it('startScan initiates scan and sets state', async () => {
    vi.useFakeTimers()
    vi.mocked(startScan).mockResolvedValue({ job_id: 'scan-1', status: 'running' })
    vi.mocked(getScanResult).mockResolvedValue({
      job_id: 'scan-1',
      status: 'completed',
      result: [{ mac: 'FF:00:11:22:33:44', name: 'BT Speaker', rssi: -60, is_audio: true, paired: false }],
    })

    const store = useBluetoothStore()
    await store.startScan('hci0')

    expect(store.scanning).toBe(true)
    expect(store.scanJobId).toBe('scan-1')

    await vi.advanceTimersByTimeAsync(1000)

    expect(store.scanning).toBe(false)
    expect(store.scanResults).toHaveLength(1)
    expect(store.scanResults[0].name).toBe('BT Speaker')

    vi.useRealTimers()
  })

  it('pairDevice sets pairing state and calls API', async () => {
    const store = useBluetoothStore()
    await store.pairDevice('FF:00:11:22:33:44', 'hci0')
    expect(pairDevice).toHaveBeenCalledWith('FF:00:11:22:33:44', 'hci0')
    expect(store.pairing).toBe(false)
    expect(store.pairTarget).toBeNull()
  })

  it('pairDevice resets state on failure', async () => {
    vi.mocked(pairDevice).mockRejectedValueOnce(new Error('pair failed'))
    const store = useBluetoothStore()
    await expect(store.pairDevice('FF:00:11:22:33:44')).rejects.toThrow()
    expect(store.pairing).toBe(false)
    expect(store.pairTarget).toBeNull()
  })

  it('rebootAdapter calls API', async () => {
    const store = useBluetoothStore()
    await store.rebootAdapter('hci0')
    expect(rebootAdapter).toHaveBeenCalledWith('hci0')
  })

  it('stopPolling clears scan interval', async () => {
    vi.useFakeTimers()
    vi.mocked(startScan).mockResolvedValue({ job_id: 'scan-2', status: 'running' })
    vi.mocked(getScanResult).mockResolvedValue({ job_id: 'scan-2', status: 'running' })

    const store = useBluetoothStore()
    await store.startScan()

    expect(store.scanning).toBe(true)
    store.stopPolling()

    // Advance time — no more polls should happen
    await vi.advanceTimersByTimeAsync(5000)
    // getScanResult should have been called at most once (if timer fired before stop)
    expect(vi.mocked(getScanResult).mock.calls.length).toBeLessThanOrEqual(1)

    vi.useRealTimers()
  })
})
