import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceTimingPanel from '@/components/devices/DeviceTimingPanel.vue'
import en from '@/i18n/en.json'
import { setLatency } from '@/api/devices'
import { setMetronome } from '@/api/calibration'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/api/devices', () => ({ setLatency: vi.fn().mockResolvedValue({ field: 'static_delay_ms', value: 0 }) }))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn().mockResolvedValue({ active: true }),
  createSession: vi.fn(),
  uploadRecording: vi.fn(),
  endSession: vi.fn(),
}))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ devices: [] }) }))

function mountPanel(overrides: Parameters<typeof makeDevice>[0] = {}) {
  return mount(DeviceTimingPanel, {
    props: { device: makeDevice({ audio: { static_delay_ms: 120, has_sink: true }, ...overrides }) },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('DeviceTimingPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('applies a new speaker delay at once', async () => {
    const w = mountPanel()
    const input = w.findAll('input[type="number"]').find((i) => (i.element as HTMLInputElement).value === '120')!
    await input.setValue('180')
    await input.trigger('change')
    await flushPromises()
    expect(setLatency).toHaveBeenCalledWith(expect.any(String), 180, expect.objectContaining({ field: 'static_delay_ms', source: 'manual' }))
  })

  it('applies the bridge suggestion with its revision', async () => {
    const w = mountPanel({
      timing: {
        latency_suggestion: {
          suggested_static_delay_ms: 150,
          source: 'bluez_delay_report',
          confidence: 'high',
          explanation: 'The speaker reports 150 ms.',
          revision: 'r1',
          double_count_risk: false,
        },
      },
    })
    expect(w.text()).toContain('Suggested delay: 150 ms')
    await w.findAll('button').find((b) => b.text() === 'Apply')!.trigger('click')
    await flushPromises()
    expect(setLatency).toHaveBeenCalledWith(expect.any(String), 150, expect.objectContaining({ source: 'bluez_delay_report', revision: 'r1' }))
  })

  it('starts the click track on this speaker', async () => {
    const w = mountPanel()
    await w.findAll('button').find((b) => b.text() === 'Start')!.trigger('click')
    expect(setMetronome).toHaveBeenCalledWith(expect.any(String), 'start')
  })
})
