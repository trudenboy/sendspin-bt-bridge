import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/updates', () => ({ getUpdateInfo: vi.fn(), startUpdateCheck: vi.fn(), applyUpdate: vi.fn() }))
vi.mock('@/composables/useIngress', () => ({ useIngress: () => ({ basePath: '', apiBase: '' }) }))

import { applyUpdate } from '@/api/updates'
import { useUpdateStore } from '@/stores/update'
import { useBridgeStore } from '@/stores/bridge'

describe('applying an update', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('follows the restart that the update causes, with the banner saying it is an update', async () => {
    vi.mocked(applyUpdate).mockResolvedValue({ success: true, message: 'ok', started: true })
    const bridge = useBridgeStore()
    const store = useUpdateStore()
    store.info = { update_available: true, runtime: 'systemd', auto_update: false, channel: 'stable', update_method: 'one_click' } as never
    await store.doApplyUpdate()
    expect(bridge.restartState).toBe('stopping')
    expect(bridge.restartReason).toBe('update')
  })

  it('does not announce a restart when the update did not start', async () => {
    vi.mocked(applyUpdate).mockResolvedValue({ success: true, message: 'nothing', started: false })
    const bridge = useBridgeStore()
    const store = useUpdateStore()
    store.info = { update_available: true, runtime: 'systemd', auto_update: false, channel: 'stable', update_method: 'one_click' } as never
    await store.doApplyUpdate()
    expect(bridge.restartState).toBe('idle')
  })

  it('a plain restart is not an update', () => {
    const bridge = useBridgeStore()
    bridge.initiateRestart()
    expect(bridge.restartReason).toBe('restart')
  })
})
