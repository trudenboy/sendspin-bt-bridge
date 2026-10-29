import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/bluetooth', () => ({
  getAdapters: vi.fn(), getKnownDevices: vi.fn(), getBtDeviceInfo: vi.fn(), forgetBtDevice: vi.fn(),
  setAdapterPower: vi.fn(), startScan: vi.fn(), startPairing: vi.fn(), startReset: vi.fn(),
}))
vi.mock('@/api/config', () => ({ getConfig: vi.fn(), saveConfig: vi.fn() }))
vi.mock('@/api/jobs', () => ({ cancelJob: vi.fn().mockResolvedValue(undefined), getJob: vi.fn() }))

import * as bt from '@/api/bluetooth'
import { cancelJob, getJob } from '@/api/jobs'
import { useBluetoothStore } from '@/stores/bluetooth'
import type { Job } from '@/api/types'

const job = (status: Job['status']) => ({ id: 'j1', kind: 'k', status, created_at: '', updated_at: '', progress: {}, error: null, result: null }) as unknown as Job

describe('cancelling a scan or pairing', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('asks the bridge to cancel the running scan, and a cancelled scan is not an error', async () => {
    vi.mocked(bt.startScan).mockResolvedValue(job('running'))
    vi.mocked(getJob).mockResolvedValue(job('cancelled'))
    vi.useFakeTimers()
    const store = useBluetoothStore()
    const scan = store.startScan('hci0')
    await vi.advanceTimersByTimeAsync(0)
    expect(store.scanStartedAt).not.toBeNull()
    await store.cancelScan()
    expect(cancelJob).toHaveBeenCalledWith('j1')
    expect(store.cancelling).toBe(true)
    await vi.advanceTimersByTimeAsync(2000)
    await scan
    vi.useRealTimers()
    expect(store.scanError).toBeNull()
    expect(store.scanning).toBe(false)
    expect(store.cancelling).toBe(false)
  })

  it('a cancelled pairing ends without a pairing error', async () => {
    vi.mocked(bt.startPairing).mockResolvedValue(job('running'))
    vi.mocked(getJob).mockResolvedValue(job('cancelled'))
    vi.useFakeTimers()
    const store = useBluetoothStore()
    const pairing = store.pairDevice('AA:BB')
    await vi.advanceTimersByTimeAsync(0)
    await store.cancelPairing()
    expect(cancelJob).toHaveBeenCalledWith('j1')
    await vi.advanceTimersByTimeAsync(2000)
    expect(await pairing).toBe(false)
    vi.useRealTimers()
    expect(store.pairError).toBeNull()
  })
})
