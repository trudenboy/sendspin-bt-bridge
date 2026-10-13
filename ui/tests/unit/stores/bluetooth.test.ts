import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/bluetooth', () => ({
  getAdapters: vi.fn(),
  getKnownDevices: vi.fn(),
  getBtDeviceInfo: vi.fn(),
  forgetBtDevice: vi.fn().mockResolvedValue(undefined),
  setAdapterPower: vi.fn().mockResolvedValue({ applied: true }),
  startScan: vi.fn(),
  startPairing: vi.fn(),
  startReset: vi.fn(),
}))
vi.mock('@/api/config', () => ({ getConfig: vi.fn(), saveConfig: vi.fn().mockResolvedValue({}) }))

import * as bt from '@/api/bluetooth'
import { ApiError } from '@/api/client'
import { getConfig, saveConfig } from '@/api/config'
import { useBluetoothStore } from '@/stores/bluetooth'
import type { Job } from '@/api/types'

const job = (status: Job['status'], extra: Record<string, unknown> = {}) =>
  ({ id: 'j', kind: 'k', status, created_at: '', updated_at: '', progress: {}, ...extra }) as Job

describe('useBluetoothStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('scans one adapter and keeps the devices the job found', async () => {
    vi.mocked(bt.startScan).mockResolvedValue(
      job('succeeded', { result: { devices: [{ mac: 'AA', name: 'Speaker', adapter: 'M' }], stats: {} } }),
    )
    const store = useBluetoothStore()
    await store.startScan('hci1', false)
    expect(bt.startScan).toHaveBeenCalledWith('hci1', false)
    expect(store.scanResults.map((d) => d.mac)).toEqual(['AA'])
    expect(store.scanning).toBe(false)
  })

  it('reports why a scan was refused', async () => {
    vi.mocked(bt.startScan).mockRejectedValue(new ApiError({ status: 429, code: 'scan_cooldown', detail: 'Scan cooldown active' }))
    const store = useBluetoothStore()
    await store.startScan('hci0')
    expect(store.scanError).toBe('Scan cooldown active')
  })

  it('reports a failed scan job', async () => {
    vi.mocked(bt.startScan).mockResolvedValue(job('failed', { error: { code: 'discovery_refused', detail: 'Adapter busy', status: 502 } }))
    const store = useBluetoothStore()
    await store.startScan('hci0')
    expect(store.scanError).toBe('Adapter busy')
  })

  it('pairs with the per-attempt options and says whether it worked', async () => {
    vi.mocked(bt.startPairing).mockResolvedValue(job('succeeded'))
    const store = useBluetoothStore()
    expect(await store.pairDevice('AA', { adapter: 'hci0', noInputNoOutputAgent: true })).toBe(true)
    expect(bt.startPairing).toHaveBeenCalledWith('AA', { adapter: 'hci0', noInputNoOutputAgent: true })

    vi.mocked(bt.startPairing).mockResolvedValue(job('failed', { error: { code: 'pin_required', detail: 'PIN', status: 422 } }))
    expect(await store.pairDevice('AA')).toBe(false)
    expect(store.pairError).toBe('PIN')
    expect(store.pairing).toBe(false)
  })

  it('adds a paired device as a Bluetooth player once, without an id', async () => {
    const existing = { id: 'p-bb', name: 'x', enabled: true, backend: { type: 'bluetooth_a2dp', mac: 'BB:00', adapter: '' } }
    vi.mocked(getConfig).mockResolvedValue({ BRIDGE_NAME: 'B', players: [existing] } as never)
    const store = useBluetoothStore()
    expect(await store.addToBridge('aa:00', 'Kitchen', 'hci1')).toBe(true)
    expect(saveConfig).toHaveBeenCalledWith({
      BRIDGE_NAME: 'B',
      players: [
        existing,
        { name: 'Kitchen', enabled: true, backend: { type: 'bluetooth_a2dp', mac: 'AA:00', adapter: 'hci1' } },
      ],
    })

    vi.mocked(saveConfig).mockClear()
    expect(await store.addToBridge('bb:00', 'dup')).toBe(false)
    expect(saveConfig).not.toHaveBeenCalled()
  })

  it('does not mistake a pulse_sink player for a MAC duplicate', async () => {
    vi.mocked(getConfig).mockResolvedValue({
      players: [{ id: 'p', name: 'Sink', backend: { type: 'pulse_sink', sink: 'AA:00' } }],
    } as never)
    const store = useBluetoothStore()
    expect(await store.addToBridge('AA:00', 'Desk')).toBe(true)
  })

  it('stores the room chosen when adding', async () => {
    vi.mocked(getConfig).mockResolvedValue({ players: [] } as never)
    const store = useBluetoothStore()
    await store.addToBridge('CC:00', 'Desk', 'hci0', { room: ' Office ' })
    expect(saveConfig).toHaveBeenCalledWith({
      players: [
        { name: 'Desk', enabled: true, backend: { type: 'bluetooth_a2dp', mac: 'CC:00', adapter: 'hci0' }, room_name: 'Office' },
      ],
    })
  })

  it('powers an adapter and refreshes the list', async () => {
    vi.mocked(bt.getAdapters).mockResolvedValue([{ id: 'hci0', mac: 'M', name: 'n', powered: false }] as never)
    const store = useBluetoothStore()
    await store.setPower('hci0', false)
    expect(bt.setAdapterPower).toHaveBeenCalledWith('hci0', false)
    expect(store.adapters[0]!.powered).toBe(false)
  })

  it('forgets a bond and drops it from the list', async () => {
    vi.mocked(bt.getKnownDevices).mockResolvedValue([{ mac: 'AA', name: 'a', adapters: [] }, { mac: 'BB', name: 'b', adapters: [] }])
    const store = useBluetoothStore()
    await store.fetchPairedDevices()
    await store.removePairedDevice('AA')
    expect(store.pairedDevices.map((d) => d.mac)).toEqual(['BB'])
  })
})
