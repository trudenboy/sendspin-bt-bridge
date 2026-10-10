import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ConnectionBanner from '@/components/layout/ConnectionBanner.vue'
import en from '@/i18n/en.json'

const bridge = reactive({ sseConnected: true, restartState: 'idle' })
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => bridge }))

function mountBanner() {
  return mount(ConnectionBanner, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
}

describe('ConnectionBanner', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    bridge.sseConnected = true
    bridge.restartState = 'idle'
  })
  afterEach(() => vi.useRealTimers())

  it('stays quiet through the routine reconnect of the event stream', async () => {
    const w = mountBanner()
    bridge.sseConnected = false
    await nextTick()
    vi.advanceTimersByTime(2000)
    bridge.sseConnected = true
    await nextTick()
    vi.advanceTimersByTime(5000)
    await nextTick()
    expect(w.text()).toBe('')
  })

  it('says so when the bridge stays unreachable, and clears on reconnect', async () => {
    const w = mountBanner()
    bridge.sseConnected = false
    await nextTick()
    vi.advanceTimersByTime(4500)
    await nextTick()
    expect(w.text()).toContain('Lost connection to the bridge')
    bridge.sseConnected = true
    await nextTick()
    expect(w.text()).toBe('')
  })

  it('leaves a restart to the restart banner', async () => {
    const w = mountBanner()
    bridge.restartState = 'restarting'
    bridge.sseConnected = false
    await nextTick()
    vi.advanceTimersByTime(10000)
    await nextTick()
    expect(w.text()).toBe('')
  })
})
