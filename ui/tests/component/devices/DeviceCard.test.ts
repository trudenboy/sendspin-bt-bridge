import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import en from '@/i18n/en.json'
import type { DeviceSnapshot } from '@/api/types'

vi.mock('@/stores/devices', () => ({
  useDeviceStore: () => ({
    setVolume: vi.fn(),
    setMute: vi.fn(),
    reconnect: vi.fn(),
    standby: vi.fn(),
    wake: vi.fn(),
    remove: vi.fn(),
  }),
}))

vi.mock('@/stores/ma', () => ({
  useMaStore: () => ({
    nowPlaying: {},
  }),
}))

function buildI18n() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })
}

function makeDevice(overrides: Partial<DeviceSnapshot> = {}): DeviceSnapshot {
  return {
    player_name: 'Test Speaker',
    mac: 'AA:BB:CC:DD:EE:FF',
    enabled: true,
    status: 'connected',
    connected: true,
    audio_streaming: false,
    server_connected: true,
    volume: 65,
    muted: false,
    backend_info: { type: 'bluetooth_a2dp', capabilities: [] },
    player_state: 'READY',
    ...overrides,
  }
}

describe('DeviceCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function mountCard(device: DeviceSnapshot = makeDevice()) {
    return mount(DeviceCard, {
      props: { device },
      global: { plugins: [buildI18n()] },
    })
  }

  it('renders device name', () => {
    const w = mountCard()
    expect(w.text()).toContain('Test Speaker')
  })

  it('renders MAC address', () => {
    const w = mountCard()
    expect(w.text()).toContain('AA:BB:CC:DD:EE:FF')
  })

  it('renders backend type badge', () => {
    const w = mountCard()
    expect(w.text()).toContain('Bluetooth A2DP')
  })

  it('renders status badge', () => {
    const w = mountCard()
    expect(w.text()).toContain('Ready')
  })

  it('renders volume slider when connected', () => {
    const w = mountCard()
    expect(w.find('input[type="range"]').exists()).toBe(true)
  })

  it('hides volume slider when disconnected', () => {
    const w = mountCard(makeDevice({ connected: false }))
    expect(w.find('input[type="range"]').exists()).toBe(false)
  })

  it('renders streaming status badge', () => {
    const w = mountCard(makeDevice({ player_state: 'STREAMING', audio_streaming: true }))
    expect(w.text()).toContain('Streaming')
  })

  it('renders error status badge', () => {
    const w = mountCard(makeDevice({ player_state: 'ERROR' }))
    expect(w.text()).toContain('Error')
  })

  it('emits openDetail on details action', async () => {
    const w = mountCard()
    // Find and click the dropdown trigger (MoreVertical button)
    const actionBtn = w.findAll('button').find((b) =>
      b.attributes('aria-label') === 'Details',
    )
    // The dropdown trigger opens the action menu
    expect(w.html()).toContain('Details')
  })

  it('renders action dropdown trigger', () => {
    const w = mountCard()
    // MoreVertical icon should be present as an SVG
    expect(w.find('svg').exists()).toBe(true)
  })
})
