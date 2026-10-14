import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceListRow from '@/components/devices/DeviceListRow.vue'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/stores/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/stores/devices')>()),
  useDeviceStore: () => ({
    setVolume: vi.fn(),
    setMute: vi.fn(),
    setEnabled: vi.fn(),
    reconnect: vi.fn(),
    standby: vi.fn(),
    wake: vi.fn(),
    forget: vi.fn(),
    release: vi.fn(),
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
  transport: vi.fn().mockResolvedValue(undefined),
}))

function buildI18n() {
  return createI18n({ legacy: false, locale: 'en', messages: { en } })
}

describe('DeviceListRow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  function mountRow(device = makeDevice()) {
    return mount(DeviceListRow, {
      props: { device },
      global: { plugins: [buildI18n()] },
    })
  }

  it('renders device name', () => {
    const w = mountRow()
    expect(w.text()).toContain('Test Speaker')
  })

  it('renders status badge', () => {
    const w = mountRow()
    expect(w.text()).toContain('Connected')
  })

  it('renders volume slider when connected', () => {
    const w = mountRow()
    expect(w.find('input[type="range"]').exists()).toBe(true)
  })

  it('shows dash when disconnected', () => {
    const w = mountRow(makeDevice({ bluetooth: { connected: false } }))
    expect(w.find('input[type="range"]').exists()).toBe(false)
    expect(w.text()).toContain('—')
  })

  it('shows transport controls when streaming', () => {
    const w = mountRow(
      makeDevice({ playback: { playing: true }, audio: { streaming: true } }),
    )
    const pauseBtn = w.findAll('button').find(
      (b) => b.attributes('aria-label') === 'Pause',
    )
    expect(pauseBtn).toBeTruthy()
  })

  it('hides transport controls when not streaming', () => {
    const w = mountRow(makeDevice())
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
    expect(w.emitted('openDetail')![0]).toEqual(['dev-1'])
  })

  it('shows adapter in hidden column', () => {
    const w = mountRow(makeDevice())
    expect(w.text()).toContain('hci0')
  })

  describe('backend', () => {
    const output = () =>
      makeDevice({
        backend: { type: 'pulse_sink', sink: 'alsa_output.usb', mac: null, adapter: null },
        bluetooth: null as never,
        health: { state: 'unknown' },
        audio: { sink_name: 'alsa_output.usb', has_sink: true, sink_missing: false },
      })

    it('marks a Bluetooth speaker with the Bluetooth icon', () => {
      expect(mountRow().find('[data-backend-icon]').attributes('title')).toBe('Bluetooth speaker')
    })

    it('marks a local audio output with the output icon and names its output', () => {
      const w = mountRow(output())
      expect(w.find('[data-backend-icon]').attributes('title')).toBe('Audio output')
      expect(w.find('[data-chip="output"]').text()).toContain('alsa_output.usb')
      expect(w.find('[data-chip="adapter"]').exists()).toBe(false)
    })
  })

  it('shows the sync badge while streaming', () => {
    const w = mountRow(makeDevice({ playback: { playing: true }, audio: { streaming: true }, timing: { available: true } }))
    expect(w.find('[data-chip="sync"]').text()).toContain('Sync')
  })

  it('shows the group chip', () => {
    const w = mountRow(makeDevice({ music_assistant: { syncgroup_id: 'syncgroup_abcd1234' }, playback: { group: { id: 'syncgroup_abcd1234', name: 'Downstairs' } } }))
    expect(w.find('[data-chip="group"]').text()).toContain('Downstairs')
  })

  it('flags unavailable controls with their reasons in a tooltip', () => {
    const w = mountRow(
      makeDevice({
        capabilities: {
          actions: { volume: { supported: true, currently_available: false, blocked_reason: 'No sink.', depends_on: [], recommended_action: null } },
        },
      }),
    )
    const flag = w.find('[data-blocked-flag]')
    expect(flag.attributes('title')).toContain('Volume unavailable')
    expect(flag.attributes('title')).toContain('No sink.')
  })

  it('shows signal bars for a connected speaker', () => {
    const w = mountRow(makeDevice({ bluetooth: { rssi_dbm: -5 } }))
    expect(w.findAll('[data-signal-bar].is-on')).toHaveLength(3)
  })
})
