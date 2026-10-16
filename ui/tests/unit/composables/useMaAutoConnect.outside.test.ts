import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { nextTick } from 'vue'
import { useBridgeStore } from '@/stores/bridge'
import { useMaStore } from '@/stores/ma'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/api/ma', () => ({
  getConnection: vi.fn().mockResolvedValue({ connected: false, configured_url: 'http://ma:8095' }),
  silentAuth: vi.fn().mockResolvedValue({ connected: true }),
  getGroups: vi.fn().mockResolvedValue([]),
  discoverMA: vi.fn(),
}))

import * as api from '@/api/ma'
import { useMaAutoConnect } from '@/composables/useMaAutoConnect'

// Opened directly, not through Home Assistant ingress — even with HA's element around.
window.history.replaceState({}, '', '/groups')
const haElement = document.createElement('home-assistant') as HTMLElement & { hass?: unknown }
haElement.hass = { auth: { data: { access_token: 'ha-user-token' } } }
document.body.appendChild(haElement)

describe('useMaAutoConnect outside Home Assistant ingress', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('never signs in by itself and keeps a manual sign-in on a connection loss', async () => {
    const bridge = useBridgeStore()
    const ma = useMaStore()
    bridge.snapshot = makeStatus()
    bridge.snapshot.bridge.ma_connected = true
    ma.connected = true
    useMaAutoConnect()
    await nextTick()

    const lost = makeStatus()
    lost.bridge.ma_connected = false
    bridge.snapshot = lost
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))

    expect(api.silentAuth).not.toHaveBeenCalled()
    expect(ma.connected).toBe(true)
  })
})
