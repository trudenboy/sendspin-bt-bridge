import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import { useBridgeStore } from '@/stores/bridge'
import { setTechnicalDetails } from '@/composables/useTechnicalDetails'
import en from '@/i18n/locales/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => ({ setVolume: vi.fn(), setMute: vi.fn(), release: vi.fn(), setEnabled: vi.fn() }),
}))
vi.mock('@/stores/notifications', () => ({
  useNotificationStore: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))

const Stub = { template: '<div />' }

function mountCard(device: Device) {
  useBridgeStore().snapshot = makeStatus([device])
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: Stub }] })
  return mount(DeviceCard, {
    props: { device },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router] },
  })
}

const busy = (extra: Record<string, unknown> = {}) =>
  makeDevice({
    playback: { playing: true, track: { title: 'Song', artist: 'Band' }, server_connected: true, server: { name: 'MA', host: '10.0.0.2', own: true } },
    audio: { streaming: true },
    timing: { available: true, sync_error_ms: 3.2, reanchor_count: 4 },
    bluetooth: { connected: true, rssi_dbm: -5, rssi_at: new Date().toISOString() },
    transfer_readiness: { ready: false, reason: 'bluetooth_disconnected', severity: 'error' },
    ...extra,
  })

describe('technical details on a device card', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('off: keeps the plain sync state and hides the technical chips', () => {
    setTechnicalDetails(false)
    const w = mountCard(busy())
    expect(w.find('[data-chip="sync"]').text()).toBe('Sync')
    expect(w.find('[data-chip="reanchors"]').exists()).toBe(false)
    expect(w.find('[data-chip="signal"]').exists()).toBe(false)
    expect(w.find('[data-chip="server"]').exists()).toBe(false)
    expect(w.find('[data-chip="transfer"]').exists()).toBe(false)
  })

  it('off: shows the plain Sync label, not Playout, for a playout-controlled stream', () => {
    setTechnicalDetails(false)
    const w = mountCard(
      busy({ timing: { available: true, playout: { mode: 'loop', state: 'locked', warm: true, error_ms: 0.4, jumps: 3 } } }),
    )
    expect(w.find('[data-chip="sync"]').text()).toBe('Sync')
    expect(w.find('[data-chip="jumps"]').exists()).toBe(false)
  })

  it('on: shows them all', () => {
    setTechnicalDetails(true)
    const w = mountCard(busy())
    expect(w.find('[data-chip="sync"]').text()).toContain('3 ms')
    expect(w.find('[data-chip="reanchors"]').exists()).toBe(true)
    expect(w.find('[data-chip="signal"]').exists()).toBe(true)
    expect(w.find('[data-chip="server"]').exists()).toBe(true)
    expect(w.find('[data-chip="transfer"]').exists()).toBe(true)
  })

  it("off: a server that is not the bridge's own still warns", () => {
    setTechnicalDetails(false)
    const w = mountCard(busy({ playback: { server: { name: 'Other', host: '10.0.0.9', own: false } } }))
    expect(w.find('[data-chip="server"]').exists()).toBe(true)
  })

  it('off: the Bluetooth and Audio link chips appear only when not OK', () => {
    setTechnicalDetails(false)
    const ok = mountCard(busy())
    expect(ok.find('[data-status-state="bluetooth"]').exists()).toBe(false)
    expect(ok.find('[data-status-state="audio_link"]').exists()).toBe(false)

    const down = mountCard(
      busy({ links: { bluetooth: { state: 'disconnected', tone: 'error', reason: null }, audio_link: { state: 'connected', tone: 'success', reason: null } } }),
    )
    expect(down.find('[data-status-state="bluetooth"]').exists()).toBe(true)
    expect(down.find('[data-status-state="audio_link"]').exists()).toBe(false)
  })

  it('on: the link chips always show', () => {
    setTechnicalDetails(true)
    const w = mountCard(busy())
    expect(w.find('[data-status-state="bluetooth"]').exists()).toBe(true)
    expect(w.find('[data-status-state="audio_link"]').exists()).toBe(true)
  })
})
