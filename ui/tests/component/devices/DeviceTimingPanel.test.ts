import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceTimingPanel from '@/components/devices/DeviceTimingPanel.vue'
import en from '@/i18n/en.json'
import { setLatency } from '@/api/devices'
import { makeDevice } from '../../fixtures/device'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('@/api/devices', () => ({ setLatency: vi.fn().mockResolvedValue({ field: 'static_delay_ms', value: 0 }) }))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  createSession: vi.fn(),
  uploadRecording: vi.fn(),
  endSession: vi.fn(),
}))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ devices: [] }) }))

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', component: { template: '<div />' } },
    { path: '/groups', name: 'groups', component: { template: '<div />' } },
    { path: '/sync/:groupId', name: 'sync', component: { template: '<div />' } },
  ],
})

function mountPanel(overrides: Parameters<typeof makeDevice>[0] = {}) {
  return mount(DeviceTimingPanel, {
    props: { device: makeDevice({ audio: { static_delay_ms: 120, has_sink: true }, ...overrides }) },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router] },
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
    expect(w.text()).toContain('Suggested latency compensation: 150 ms')
    await w.findAll('button').find((b) => b.text() === 'Apply')!.trigger('click')
    await flushPromises()
    expect(setLatency).toHaveBeenCalledWith(expect.any(String), 150, expect.objectContaining({ source: 'bluez_delay_report', revision: 'r1' }))
  })

  it('has one calibration path: no per-speaker click track or microphone comparison', () => {
    const w = mountPanel()
    expect(w.text()).not.toContain('Click track')
    expect(w.text()).not.toContain('microphone')
    expect(w.findAll('button').some((b) => b.text() === 'Start')).toBe(false)
  })

  it('links to tuning the whole Music Assistant group the player is in', async () => {
    await router.push('/')
    const w = mountPanel({ music_assistant: { syncgroup_id: 'syncgroup_abc' } })
    const link = w.findAll('a').find((a) => a.attributes('href') === '/sync/syncgroup_abc')
    expect(link).toBeTruthy()
    expect(link!.text()).toBe('Tune with your group →')
  })

  it('explains how to make a sync group, with a link to Groups, when the player is in none', async () => {
    await router.push('/')
    const w = mountPanel()
    expect(w.text()).toContain('is not in a sync group')
    expect(w.findAll('a').find((a) => a.attributes('href') === '/groups')).toBeTruthy()
    expect(w.findAll('a').some((a) => a.attributes('href')?.startsWith('/sync/'))).toBe(false)
  })
  it('lists the playout measurements, with the fallback reason when it fell back', () => {
    const playout = { mode: 'measure', state: 'playing', warm: true, warm_s: 90, warmup_s: 60, error_ms: 0.4, error_p95_ms: 1.1, error_slope_ms_per_s: 0.02, corrections_per_s: 0.5, jumps: 1, underruns: 0, pa_latency_ms: 800, host_latency_ms: 12, sink_rate_ppm: 3, clock_slew_ppm: -2 }
    const w = mountPanel({ timing: { playout } })
    expect(w.text()).toContain('Playout')
    expect(w.text()).toContain('0.4 ms')
    expect(w.text()).toContain('p95')
    expect(w.find('[data-testid="timing-latency"]').text()).toContain('800 ms')
    expect(w.find('[data-testid="playout-fallback"]').exists()).toBe(false)
    const f = mountPanel({ timing: { playout: { ...playout, state: 'fallback', fallback_reason: 'no_sink' } } })
    expect(f.find('[data-testid="playout-fallback"]').text()).toContain('no_sink')
  })

  it('hides the playout block when there is no playout timing', () => {
    expect(mountPanel({ timing: { playout: null } }).find('[data-testid="playout-block"]').exists()).toBe(false)
  })

  it('shows PulseAudio latency and clock slew as plain rows, without an empty playout block, in off mode', () => {
    const w = mountPanel({ timing: { playout: { mode: 'off' }, pa_latency_ms: 812, clock_slew_ppm: -1.5 } })
    expect(w.find('[data-testid="playout-block"]').exists()).toBe(false)
    const rows = w.find('[data-testid="timing-latency"]')
    expect(rows.text()).toContain('812 ms')
    expect(rows.text()).toContain('-1.5 ppm')
    expect(w.text()).not.toContain('—')
  })

  it('shows the plain latency rows also when there is no playout status at all', () => {
    const w = mountPanel({ timing: { playout: null, pa_latency_ms: 790 } })
    expect(w.find('[data-testid="timing-latency"]').text()).toContain('790 ms')
    expect(w.find('[data-testid="timing-latency"]').text()).not.toContain('Clock slew')
  })
})
