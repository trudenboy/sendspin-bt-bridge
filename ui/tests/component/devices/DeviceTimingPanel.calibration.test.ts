import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceTimingPanel from '@/components/devices/DeviceTimingPanel.vue'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/api/devices', () => ({ setLatency: vi.fn(), forgetAirCalibration: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(),
  createSession: vi.fn(),
  uploadRecording: vi.fn(),
  endSession: vi.fn(),
}))
const refresh = vi.fn().mockResolvedValue(undefined)
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ devices: [], refresh }) }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
import { forgetAirCalibration } from '@/api/devices'
import { confirmDialog } from '@/composables/useConfirm'

const ADAPTER = 'C0:FB:F9:62:D7:D6'
const entry = (over: Record<string, unknown> = {}) => ({
  codec: 'sbc',
  rate: 44100,
  adapter: ADAPTER,
  latency_ms: 203.4,
  spread_ms: null,
  measured_at: new Date(Date.now() - 2 * 86_400_000 - 60_000).toISOString(),
  reference: '',
  source: 'sync_wizard',
  ...over,
})

function mountPanel(entries: unknown[], opts: { locale?: 'en' | 'ru'; air?: Record<string, unknown> | null } = {}) {
  const device = makeDevice({
    id: 'dev-1',
    audio: { air_to_sound_latency: entries } as never,
    bluetooth: { adapter: { mac: ADAPTER, hci: 'hci1', name: null } } as never,
    timing: { air_sensor: opts.air ?? null } as never,
  })
  return mount(DeviceTimingPanel, {
    props: { device },
    global: { plugins: [createI18n({ legacy: false, locale: opts.locale ?? 'en', messages: { en, ru } })] },
  })
}
const section = (w: ReturnType<typeof mountPanel>) => w.find('[data-testid="air-calibrations"]')

describe('DeviceTimingPanel air calibrations', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(forgetAirCalibration).mockClear()
    vi.mocked(confirmDialog).mockClear().mockResolvedValue(true)
    refresh.mockClear()
  })

  it('shows nothing without calibrations', () => {
    expect(section(mountPanel([])).exists()).toBe(false)
  })

  it('shows one line per calibration with codec, rate, adapter and age', () => {
    const text = section(mountPanel([entry()])).text()
    expect(text).toContain('Speaker latency: 203.4 ms · SBC 44.1 kHz · hci1 · 2 days ago')
  })

  it('names another adapter by its MAC', () => {
    const text = section(mountPanel([entry({ adapter: 'C0:FB:F9:62:D6:9D', rate: 48000, latency_ms: 150 })])).text()
    expect(text).toContain('150.0 ms · SBC 48 kHz · C0:FB:F9:62:D6:9D')
  })

  it('is shown with the air sensor off too, so a calibration can still be forgotten', () => {
    expect(section(mountPanel([entry()], { air: null })).exists()).toBe(true)
  })

  it('forgets a calibration after confirmation and refreshes', async () => {
    const w = mountPanel([entry()])
    await section(w).find('[data-testid="air-calibration-forget"]').trigger('click')
    await flushPromises()
    expect(confirmDialog).toHaveBeenCalledTimes(1)
    expect(vi.mocked(confirmDialog).mock.calls[0]![0].danger).toBe(true)
    expect(forgetAirCalibration).toHaveBeenCalledWith('dev-1', 'sbc', 44100, ADAPTER)
    expect(refresh).toHaveBeenCalled()
  })

  it('keeps the calibration when the dialog is cancelled', async () => {
    vi.mocked(confirmDialog).mockResolvedValue(false)
    const w = mountPanel([entry()])
    await section(w).find('[data-testid="air-calibration-forget"]').trigger('click')
    await flushPromises()
    expect(forgetAirCalibration).not.toHaveBeenCalled()
  })

  it('has Russian text', () => {
    const text = section(mountPanel([entry()], { locale: 'ru' })).text()
    expect(text).not.toMatch(/timing\.air/)
    expect(text).toContain('203.4')
    expect(text).toContain('Забыть')
  })
})
