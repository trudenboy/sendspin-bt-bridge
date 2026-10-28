import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceTimingPanel from '@/components/devices/DeviceTimingPanel.vue'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/api/devices', () => ({ setLatency: vi.fn() }))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(),
  createSession: vi.fn(),
  uploadRecording: vi.fn(),
  endSession: vi.fn(),
}))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ devices: [] }) }))

const locked = {
  state: 'locked',
  reason: null,
  host_placement_ms: 12.4,
  jitter_ms: 0.8,
  air_error_ms: -3.1,
  calibrated: true,
  confidence: 0.87,
  psr: 1.31,
  last_confirmed_at: new Date(Date.now() - 3000).toISOString(),
}

function mountPanel(airSensor: Record<string, unknown> | null, locale: 'en' | 'ru' = 'en') {
  return mount(DeviceTimingPanel, {
    props: { device: makeDevice({ timing: { air_sensor: airSensor } as never }) },
    global: { plugins: [createI18n({ legacy: false, locale, messages: { en, ru } })] },
  })
}
const block = (w: ReturnType<typeof mountPanel>) => w.find('[data-testid="air-sensor-block"]')

describe('DeviceTimingPanel air sensor block', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('renders nothing when the sensor is null', () => {
    expect(block(mountPanel(null)).exists()).toBe(false)
  })

  it('shows the locked state with signed values, confidence and PSR', () => {
    const text = block(mountPanel(locked)).text()
    expect(text).toContain('Locked')
    expect(text).toContain('+12.4 ms (±0.8)')
    expect(text).toContain('−3.1 ms')
    expect(text).toContain('87 %')
    expect(text).toContain('1.31')
    expect(text).toContain('3 s ago')
  })

  it('asks for calibration when the air error is not calibrated', () => {
    const text = block(mountPanel({ ...locked, calibrated: false, air_error_ms: null })).text()
    expect(text).toContain('Needs calibration')
    expect(text).not.toContain('−3.1')
  })

  it('explains an unavailable sensor with the reason', () => {
    const w = mountPanel({ state: 'unavailable', reason: 'cpu_budget' })
    expect(block(w).text()).toContain('Unavailable')
    expect(block(w).text()).toContain('too much processor time')
    expect(block(w).text()).not.toContain('ms')
  })

  it('explains an unsupported codec', () => {
    const w = mountPanel({ state: 'unsupported_codec', reason: 'codec:aac' })
    expect(block(w).text()).toContain('AAC')
  })

  it('explains why the sensor is off', () => {
    const w = mountPanel({ state: 'off', reason: 'needs_measure' })
    expect(block(w).text()).toContain('Off')
    expect(block(w).text()).toContain('measure')
  })

  it('has Russian text for the state and reason', () => {
    const w = mountPanel({ state: 'unavailable', reason: 'sbc_params' }, 'ru')
    expect(block(w).text()).not.toMatch(/timing\.air/)
    expect(block(w).text()).toContain('Недоступен')
  })
})

describe('DeviceTimingPanel air sensor reasons match their producers', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it.each([
    ['no_cap_net_raw', 'CAP_NET_RAW'],
    ['unsupported_platform', 'Linux'],
    ['input:96000/24', '44.1 and 48 kHz'],
  ])('explains %s', (reason, expected) => {
    const text = block(mountPanel({ state: 'unavailable', reason })).text()
    expect(text).toContain(expected)
    expect(text).not.toContain('Reason:')
  })

  it.each(['no_cap_net_raw', 'unsupported_platform', 'input:44100/8'])('has Russian text for %s', (reason) => {
    const text = block(mountPanel({ state: 'unavailable', reason }, 'ru')).text()
    expect(text).not.toContain('Причина:')
    expect(text).not.toMatch(/timing\.air/)
  })

  it('keeps the English and Russian air keys in sync, without unused ones', () => {
    const keys = (o: Record<string, unknown>, p = ''): string[] =>
      Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v as Record<string, unknown>, `${p}${k}.`) : [`${p}${k}`]))
    const enAir = keys((en as any).timing.air).sort()
    expect(keys((ru as any).timing.air).sort()).toEqual(enAir)
    expect(enAir).not.toContain('rows.reason')
    expect(enAir).not.toContain('reasons.no_monitor')
  })
})
