import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceListRow from '@/components/devices/DeviceListRow.vue'
import en from '@/i18n/en.json'
import type { DeviceSnapshot } from '@/api/types'

vi.mock('@/stores/devices', () => ({
  useDeviceStore: () => ({
    setVolume: vi.fn(),
    setMute: vi.fn(),
    setEnabled: vi.fn(),
    reconnect: vi.fn(),
    standby: vi.fn(),
    wake: vi.fn(),
    remove: vi.fn(),
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
  return createI18n({ legacy: false, locale: 'en', messages: { en } })
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

describe('DeviceListRow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  function mountRow(device = makeDevice()) {
    return mount(DeviceListRow, {
      props: { device, deviceIndex: 0 },
      global: { plugins: [buildI18n()] },
    })
  }

  it('renders device name', () => {
    const w = mountRow()
    expect(w.text()).toContain('Test Speaker')
  })

  it('renders status badge', () => {
    const w = mountRow()
    expect(w.text()).toContain('Ready')
  })

  it('renders volume slider when connected', () => {
    const w = mountRow()
    expect(w.find('input[type="range"]').exists()).toBe(true)
  })

  it('shows dash when disconnected', () => {
    const w = mountRow(makeDevice({ connected: false }))
    expect(w.find('input[type="range"]').exists()).toBe(false)
    expect(w.text()).toContain('—')
  })

  it('shows transport controls when streaming', () => {
    const w = mountRow(
      makeDevice({ connected: true, audio_streaming: true, player_state: 'STREAMING' }),
    )
    const pauseBtn = w.findAll('button').find(
      (b) => b.attributes('aria-label') === 'Pause',
    )
    expect(pauseBtn).toBeTruthy()
  })

  it('hides transport controls when not streaming', () => {
    const w = mountRow(makeDevice({ audio_streaming: false, player_state: 'READY' }))
    const pauseBtn = w.findAll('button').find(
      (b) => b.attributes('aria-label') === 'Pause',
    )
    expect(pauseBtn).toBeUndefined()
  })

  it('applies opacity-50 when disabled', () => {
    const w = mountRow(makeDevice({ enabled: false }))
    expect(w.find('.opacity-50').exists()).toBe(true)
  })

  it('emits openDetail on name click', async () => {
    const w = mountRow()
    const nameBtn = w.findAll('button').find(
      (b) => b.text() === 'Test Speaker',
    )
    expect(nameBtn).toBeTruthy()
    await nameBtn!.trigger('click')
    expect(w.emitted('openDetail')).toBeTruthy()
    expect(w.emitted('openDetail')![0]).toEqual(['AA:BB:CC:DD:EE:FF'])
  })

  it('shows adapter in hidden column', () => {
    const w = mountRow(makeDevice({ adapter: 'hci0' }))
    expect(w.text()).toContain('hci0')
  })
})
