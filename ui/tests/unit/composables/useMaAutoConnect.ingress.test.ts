import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { nextTick } from 'vue'
import { useBridgeStore } from '@/stores/bridge'
import { useMaStore } from '@/stores/ma'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/api/ma', () => ({
  getConnection: vi.fn().mockResolvedValue({ connected: false, configured_url: 'http://192.168.10.10:8095' }),
  silentAuth: vi.fn().mockResolvedValue({ connected: true }),
  getGroups: vi.fn().mockResolvedValue([]),
  discoverMA: vi.fn(),
}))

import * as api from '@/api/ma'
import { useMaAutoConnect } from '@/composables/useMaAutoConnect'

// Served through Home Assistant ingress, inside HA's frontend, which holds the user's token.
window.history.replaceState({}, '', '/api/hassio_ingress/abc123/')
const haElement = document.createElement('home-assistant') as HTMLElement & { hass?: unknown }
haElement.hass = { auth: { data: { access_token: 'ha-user-token' } } }

function status(maConnected: boolean) {
  const s = makeStatus()
  s.bridge.ma_connected = maConnected
  return s
}

async function settle() {
  for (let i = 0; i < 5; i++) await nextTick()
  await new Promise((r) => setTimeout(r, 0))
}

describe('useMaAutoConnect in the Home Assistant add-on', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    document.body.appendChild(haElement)
  })
  afterEach(() => haElement.remove())

  it('signs in by itself when the page loads without a Music Assistant connection', async () => {
    useBridgeStore().snapshot = status(false)
    useMaAutoConnect()
    await settle()

    expect(api.silentAuth).toHaveBeenCalledWith('ha-user-token', 'http://192.168.10.10:8095')
    expect(useMaStore().connected).toBe(true)
  })

  it('leaves a working connection alone', async () => {
    useBridgeStore().snapshot = status(true)
    useMaAutoConnect()
    await settle()

    expect(api.silentAuth).not.toHaveBeenCalled()
  })

  it('signs in again when the bridge loses Music Assistant (token no longer valid)', async () => {
    const bridge = useBridgeStore()
    const ma = useMaStore()
    bridge.snapshot = status(true)
    ma.connected = true
    useMaAutoConnect()
    await settle()
    expect(api.silentAuth).not.toHaveBeenCalled()

    bridge.snapshot = status(false)
    await settle()

    expect(api.silentAuth).toHaveBeenCalledTimes(1)
  })

  it('reports a failed automatic sign-in to the views', async () => {
    vi.mocked(api.silentAuth).mockRejectedValueOnce(new Error('no add-on'))
    useBridgeStore().snapshot = status(false)
    useMaAutoConnect()
    await settle()

    const ma = useMaStore()
    expect(ma.autoConnectFailed).toBe(true)
    expect(ma.autoConnecting).toBe(false)
  })
})
