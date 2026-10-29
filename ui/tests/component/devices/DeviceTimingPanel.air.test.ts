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

const ADAPTER = 'C0:FB:F9:62:D7:D6'
const learnedEntry = (samples = 6) => ({
  codec: 'sbc',
  rate: 44100,
  adapter: ADAPTER,
  source: 'learned',
  samples_ms: Array.from({ length: Math.min(samples, 8) }, () => 219.4),
  d_out_ms: Array.from({ length: Math.min(samples, 8) }, () => 184),
  latency_ms: 219.4,
  d_ref_ms: 184,
  starts: samples,
  measured_at: '2026-10-28T09:00:00+00:00',
})

function mountLoop(
  playout: Record<string, unknown>,
  opts: { air?: Record<string, unknown> | null; learned?: unknown[]; locale?: 'en' | 'ru' } = {},
) {
  const device = makeDevice({
    timing: {
      playout: { mode: 'loop', ...playout },
      air_sensor: opts.air === undefined ? { state: 'locked', codec: 'sbc', rate: 44100, adapter: ADAPTER } : opts.air,
    } as never,
    audio: { air_learned_latency: opts.learned ?? [] } as never,
    bluetooth: { adapter: { mac: ADAPTER, hci: 'hci1', name: null } } as never,
  })
  return mount(DeviceTimingPanel, {
    props: { device },
    global: { plugins: [createI18n({ legacy: false, locale: opts.locale ?? 'en', messages: { en, ru } })] },
  })
}
const correction = (w: ReturnType<typeof mountLoop>) => w.find('[data-testid="air-correction"]')

describe('DeviceTimingPanel air correction', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('names a calibrated target', () => {
    const w = mountLoop({ air_bias_state: 'active', air_target: 'calibrated', air_target_ms: 228.7, air_bias_ms: 1 })
    expect(correction(w).text()).toContain('Air correction: calibrated (228.7 ms)')
  })

  it('names a learned target with its starts', () => {
    const w = mountLoop({ air_bias_state: 'active', air_target: 'learned', air_learned_starts: 6, air_bias_ms: 1 })
    expect(correction(w).text()).toContain('Air correction: learned (6 starts)')
  })

  it('shows learning progress without a target', () => {
    const w = mountLoop({ air_bias_state: 'none', air_bias_reason: 'no_target' }, { learned: [learnedEntry(1)] })
    expect(correction(w).text()).toContain('Air correction: no data (learning: 1 of 2 starts)')
  })

  it('counts zero learned starts without an entry', () => {
    const w = mountLoop({ air_bias_state: 'none', air_bias_reason: 'no_target' })
    expect(correction(w).text()).toContain('(learning: 0 of 2 starts)')
  })

  it('says it is inactive while the sensor is not locked', () => {
    const w = mountLoop(
      { air_bias_state: 'seeding', air_target: 'calibrated', air_target_ms: 228.7, air_bias_ms: null },
      { air: { state: 'acquiring' } },
    )
    expect(correction(w).text()).toContain('inactive: not locked')
  })

  it('is not inactive when the sensor is locked', () => {
    const w = mountLoop({ air_bias_state: 'seeding', air_target: 'calibrated', air_target_ms: 228.7, air_bias_ms: null })
    expect(correction(w).text()).not.toContain('inactive')
  })

  it('shows the signed bias and the correction count', () => {
    const w = mountLoop({ air_bias_state: 'active', air_target: 'calibrated', air_target_ms: 228.7, air_bias_ms: 12.3, air_corrections: 1 })
    expect(correction(w).text()).toContain('Air bias +12.3 ms')
    expect(correction(w).text()).toContain('Air corrections 1')
  })

  it('hides the correction count at zero', () => {
    const w = mountLoop({ air_bias_state: 'active', air_target: 'calibrated', air_target_ms: 1, air_bias_ms: 1, air_corrections: 0 })
    expect(correction(w).text()).not.toContain('Air corrections')
  })

  it('shows the delay trim as an earlier placement', () => {
    const w = mountLoop({ air_bias_state: 'active', air_target: 'calibrated', air_target_ms: 1, air_bias_ms: 1, air_trim_ms: 30 })
    expect(correction(w).text()).toContain('Delay trim −30.0 ms (target 30.0 ms early)')
  })

  it('has no trim row at zero', () => {
    const w = mountLoop({ air_bias_state: 'active', air_target: 'calibrated', air_target_ms: 1, air_bias_ms: 1, air_trim_ms: 0 })
    expect(correction(w).text()).not.toContain('Delay trim')
  })

  it('shows nothing in measure mode', () => {
    const w = mountLoop({ mode: 'measure', air_bias_state: null })
    expect(correction(w).exists()).toBe(false)
  })

  it('has Russian text for every line', () => {
    const w = mountLoop(
      { air_bias_state: 'seeding', air_target: 'learned', air_learned_starts: 6, air_bias_ms: null, air_corrections: 2, air_trim_ms: 30 },
      { locale: 'ru', air: { state: 'acquiring' } },
    )
    const text = correction(w).text()
    expect(text).not.toMatch(/timing\.air/)
    expect(text).toContain('Коррекция по эфиру')
    expect(text).toContain('не захвачен')
  })

  it('has Russian text for the learning line', () => {
    const w = mountLoop({ air_bias_state: 'none', air_bias_reason: 'no_target' }, { locale: 'ru' })
    expect(correction(w).text()).not.toMatch(/timing\.air/)
    expect(correction(w).text()).toContain('нет данных')
  })
})
