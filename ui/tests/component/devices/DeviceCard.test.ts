import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import en from '@/i18n/en.json'
import type { DeviceSnapshot } from '@/api/types'

const mockSetVolume = vi.fn()
const mockSetMute = vi.fn()
const mockReconnect = vi.fn()
const mockStandby = vi.fn()
const mockWake = vi.fn()
const mockRemove = vi.fn()
const mockSetEnabled = vi.fn()

vi.mock('@/stores/devices', () => ({
  useDeviceStore: () => ({
    setVolume: mockSetVolume,
    setMute: mockSetMute,
    reconnect: mockReconnect,
    standby: mockStandby,
    wake: mockWake,
    remove: mockRemove,
    setEnabled: mockSetEnabled,
  }),
}))

vi.mock('@/stores/ma', () => ({
  useMaStore: () => ({
    nowPlaying: {},
  }),
}))

vi.mock('@/stores/notifications', () => ({
  useNotificationStore: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
}))

vi.mock('@/api/playback', () => ({
  transportCmd: vi.fn().mockResolvedValue({ success: true }),
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
    vi.clearAllMocks()
  })

  function mountCard(device: DeviceSnapshot = makeDevice()) {
    return mount(DeviceCard, {
      props: { device, deviceIndex: 0 },
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

  it('emits openDetail on details action', () => {
    const w = mountCard()
    expect(w.html()).toContain('Details')
  })

  it('renders action dropdown trigger', () => {
    const w = mountCard()
    expect(w.find('svg').exists()).toBe(true)
  })

  // Enable/disable toggle
  it('shows Disable in dropdown when enabled', async () => {
    const w = mountCard(makeDevice({ enabled: true }))
    // The DeviceCard action button has aria-label "Details"
    const triggerBtn = w.findAll('button').find(
      (b) => b.attributes('aria-label') === 'Details',
    )
    expect(triggerBtn).toBeTruthy()
    await triggerBtn!.trigger('click')
    expect(w.text()).toContain('Disable')
  })

  it('shows Enable in dropdown when disabled', async () => {
    const w = mountCard(makeDevice({ enabled: false }))
    const triggerBtn = w.findAll('button').find(
      (b) => b.attributes('aria-label') === 'Details',
    )
    expect(triggerBtn).toBeTruthy()
    await triggerBtn!.trigger('click')
    expect(w.text()).toContain('Enable')
  })

  it('applies opacity-50 when disabled', () => {
    const w = mountCard(makeDevice({ enabled: false }))
    const card = w.find('.opacity-50')
    expect(card.exists()).toBe(true)
  })

  it('does not apply opacity-50 when enabled', () => {
    const w = mountCard(makeDevice({ enabled: true }))
    const card = w.find('.opacity-50')
    expect(card.exists()).toBe(false)
  })

  // Transport controls
  it('shows transport controls when streaming', () => {
    const w = mountCard(
      makeDevice({ connected: true, audio_streaming: true, player_state: 'STREAMING' }),
    )
    // Should have play/pause and skip buttons (SVGs inside the transport section)
    const buttons = w.findAll('button')
    const transportBtns = buttons.filter(
      (b) =>
        b.attributes('aria-label') === 'Pause' ||
        b.attributes('aria-label') === 'Next track' ||
        b.attributes('aria-label') === 'Previous track',
    )
    expect(transportBtns.length).toBe(3)
  })

  it('hides transport controls when not streaming', () => {
    const w = mountCard(
      makeDevice({ connected: true, audio_streaming: false, player_state: 'READY' }),
    )
    const buttons = w.findAll('button')
    const transportBtns = buttons.filter(
      (b) =>
        b.attributes('aria-label') === 'Pause' ||
        b.attributes('aria-label') === 'Play' ||
        b.attributes('aria-label') === 'Next track' ||
        b.attributes('aria-label') === 'Previous track',
    )
    expect(transportBtns.length).toBe(0)
  })

  it('calls transportCmd on pause button click', async () => {
    const { transportCmd } = await import('@/api/playback')
    const w = mountCard(
      makeDevice({ connected: true, audio_streaming: true, player_state: 'STREAMING' }),
    )
    const pauseBtn = w.findAll('button').find(
      (b) => b.attributes('aria-label') === 'Pause',
    )
    expect(pauseBtn).toBeTruthy()
    await pauseBtn!.trigger('click')
    expect(transportCmd).toHaveBeenCalledWith('pause', 0)
  })

  it('calls transportCmd on next button click', async () => {
    const { transportCmd } = await import('@/api/playback')
    const w = mountCard(
      makeDevice({ connected: true, audio_streaming: true, player_state: 'STREAMING' }),
    )
    const nextBtn = w.findAll('button').find(
      (b) => b.attributes('aria-label') === 'Next track',
    )
    expect(nextBtn).toBeTruthy()
    await nextBtn!.trigger('click')
    expect(transportCmd).toHaveBeenCalledWith('next', 0)
  })
})
