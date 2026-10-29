import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaLoginFlow from '@/components/ma/MaLoginFlow.vue'
import en from '@/i18n/en.json'

const bridge = vi.hoisted(() => ({ bridge: { ma_web_url: null as string | null }, devices: [] as unknown[] }))
const api = vi.hoisted(() => ({
  discoverMA: vi.fn(),
  getConnection: vi.fn().mockResolvedValue({ connected: false }),
  signInWithPassword: vi.fn(), signInWithToken: vi.fn(),
}))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => bridge }))
vi.mock('@/api/ma', () => api)

const mountIt = () => mount(MaLoginFlow, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })

describe('MaLoginFlow server address', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    bridge.bridge.ma_web_url = null
    bridge.devices = []
    vi.clearAllMocks()
  })

  it('is prefilled from the Sendspin server the speakers already talk to', () => {
    bridge.devices = [{ playback: { server_url: 'ws://192.168.10.10:9000/sendspin' } }]
    expect((mountIt().get('input[type="url"]').element as HTMLInputElement).value).toBe('http://192.168.10.10:8095')
  })

  it('prefers the known Music Assistant web address', () => {
    bridge.bridge.ma_web_url = 'http://ma.local:8095'
    bridge.devices = [{ playback: { server_url: 'ws://192.168.10.10:9000/sendspin' } }]
    expect((mountIt().get('input[type="url"]').element as HTMLInputElement).value).toBe('http://ma.local:8095')
  })

  it('stays empty when no server is known', () => {
    expect((mountIt().get('input[type="url"]').element as HTMLInputElement).value).toBe('')
  })

  it('says so when Auto-Discover finds nothing', async () => {
    api.discoverMA.mockResolvedValue({ id: 'j', status: 'succeeded', result: { servers: [] } })
    const w = mountIt()
    await w.findAll('button').find((b) => b.text().includes('Auto-Discover'))!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain(en.ma.login.noServers)
  })
})
