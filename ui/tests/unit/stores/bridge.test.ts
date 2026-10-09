import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

const stream = vi.hoisted(() => ({ options: null as null | Record<string, (...a: unknown[]) => void> }))

vi.mock('@/composables/useEventStream', () => ({
  useEventStream: (options: Record<string, (...a: unknown[]) => void>) => {
    stream.options = options
    return { connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }
  },
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/bluetooth', () => ({ getAdapters: vi.fn().mockResolvedValue([{ id: 'hci0', mac: 'M', name: 'x' }]) }))

import { getStatus, restartBridge } from '@/api/status'
import { useBridgeStore } from '@/stores/bridge'
import { useJobsStore } from '@/stores/jobs'

describe('useBridgeStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads the status and adapters when connecting', async () => {
    vi.mocked(getStatus).mockResolvedValue(makeStatus() as never)
    const store = useBridgeStore()
    store.connectSSE()
    await vi.waitFor(() => expect(store.loading).toBe(false))
    expect(store.devices).toHaveLength(1)
    await vi.waitFor(() => expect(store.adapters).toHaveLength(1))
  })

  it('applies status events and hands job events to the jobs store', () => {
    const store = useBridgeStore()
    stream.options!.onEvent!({ type: 'status', data: makeStatus([makeDevice({ id: 'x' })]) })
    expect(store.deviceById('x')).toBeTruthy()
    stream.options!.onEvent!({ type: 'job', data: { id: 'j9', status: 'running' } })
    expect(useJobsStore().byId.j9).toBeTruthy()
  })

  it('tracks a restart from stopping to ready', async () => {
    const store = useBridgeStore()
    await store.restart()
    expect(restartBridge).toHaveBeenCalled()
    expect(store.restartState).toBe('stopping')
    stream.options!.onDisconnect!()
    expect(store.restartState).toBe('restarting')
    stream.options!.onConnect!()
    expect(store.restartState).toBe('ready')
  })
})
