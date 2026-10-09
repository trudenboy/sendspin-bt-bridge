import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useBridgeStore } from '@/stores/bridge'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/api/ma', () => ({ getConnection: vi.fn().mockResolvedValue({ connected: false }) }))

import { useMaAutoConnect } from '@/composables/useMaAutoConnect'

describe('useMaAutoConnect', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('starts when the status has already loaded', () => {
    useBridgeStore().snapshot = makeStatus()
    expect(() => useMaAutoConnect()).not.toThrow()
  })

  it('waits for the status otherwise', async () => {
    const bridge = useBridgeStore()
    expect(() => useMaAutoConnect()).not.toThrow()
    bridge.snapshot = makeStatus()
    await Promise.resolve()
  })
})
