import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import AirCalibrations from '@/components/sync/AirCalibrations.vue'
import { useSyncStore } from '@/stores/sync'
import { ApiError } from '@/api/client'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

vi.mock('@/api/sync', () => ({ sessionReference: vi.fn(), saveAirCalibrations: vi.fn() }))
import * as api from '@/api/sync'

const members = [
  { ma_player_id: 'kiosk', name: 'Kiosk (headset)', origin: 'sendspin', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'eneby', name: 'ENEBY @ 85b1ecde', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'lenco', name: 'Lenco', origin: 'bridge', adjustable: true, delay_ms: 0 },
]
const candidate = (over: Record<string, unknown> = {}) => ({
  member: 'eneby', eligible: true, reason: null, latency_ms: 203.4, acoustic_lag_ms: 215.4, host_placement_ms: 12,
  codec: 'sbc', rate: 44100, adapter: 'C0:FB:F9:62:D7:D6', delay_report_ms: 150, ...over,
})
const session = (over: Record<string, unknown> = {}) =>
  ({ id: 's1', group_id: 'g', state: 'tuning', members, reference: 'kiosk',
     air_calibrations: [candidate(), candidate({ member: 'lenco', eligible: false, reason: 'no_air_clicks', latency_ms: null })],
     ...over }) as never

const mountIt = (s: never, locale = 'en') =>
  mount(AirCalibrations, {
    global: { plugins: [createI18n({ legacy: false, locale, messages: { en, ru } })] },
    props: { session: s },
  })

describe('AirCalibrations', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('uses the real border token, not the non-existent border-border-default', () => {
    const cls = mountIt(session()).get('[data-air-calibrations]').classes()
    expect(cls).toContain('border-border')
    expect(cls).not.toContain('border-border-default')
  })

  it('is not shown without candidates', () => {
    expect(mountIt(session({ air_calibrations: [] })).find('[data-air-calibrations]').exists()).toBe(false)
    expect(mountIt(session({ air_calibrations: undefined })).find('[data-air-calibrations]').exists()).toBe(false)
  })

  it('shows an eligible member with its value, the delay report as a hint and a save checkbox', () => {
    const row = mountIt(session()).get('[data-air-row="eneby"]')
    expect(row.text()).toContain('ENEBY')
    expect(row.text()).toContain('Bluetooth latency measured: 203 ms')
    expect(row.text()).toContain('reports 150 ms')
    expect(row.get('[data-air-save="eneby"]').element).toBeInstanceOf(HTMLInputElement)
    expect(row.text()).toContain('Save this Bluetooth latency')
  })

  it('shows an ineligible member with its reason in words and nothing to save', () => {
    const row = mountIt(session()).get('[data-air-row="lenco"]')
    expect(row.text()).toContain('Its clicks were not found on the air')
    expect(row.find('[data-air-save="lenco"]').exists()).toBe(false)
  })

  it('every reason has words in English and Russian', () => {
    const reasons = ['not_effective', 'no_reference', 'not_measured', 'not_sbc', 'air_epoch_changed', 'no_air_clicks', 'no_written_clicks',
      'fold_failed', 'no_air_frames']
    for (const locale of ['en', 'ru']) {
      const w = mountIt(session({ air_calibrations: reasons.map((r) => candidate({ eligible: false, reason: r, latency_ms: null, member: r })) }), locale)
      for (const r of reasons) {
        const text = w.get(`[data-air-row="${r}"]`).text()
        expect(text).not.toContain('sync.air')
        expect(text.length).toBeGreaterThan(r.length + 5)
      }
    }
    expect(mountIt(session(), 'ru').text()).toContain('Сохранить эту задержку Bluetooth')
  })

  it('a value outside the stored range cannot be saved', () => {
    const w = mountIt(session({ air_calibrations: [candidate({ latency_ms: -85 })] }))
    expect(w.find('[data-air-save="eneby"]').exists()).toBe(false)
    expect(w.get('[data-air-row="eneby"]').text()).toContain('0–2000 ms')
  })

  it('saves the checked members and says so', async () => {
    vi.mocked(api.saveAirCalibrations).mockResolvedValue([candidate()] as never)
    const w = mountIt(session())
    expect(w.get('[data-air-save-button]').attributes('disabled')).toBeDefined()
    await w.get('[data-air-save="eneby"]').setValue(true)
    await w.get('[data-air-save-button]').trigger('click')
    await flushPromises()
    expect(api.saveAirCalibrations).toHaveBeenCalledWith('s1', ['eneby'])
    expect(w.get('[data-air-row="eneby"]').text()).toContain('Bluetooth latency measured: 203 ms, saved')
    expect(w.find('[data-air-save="eneby"]').exists()).toBe(false)
  })

  it('a refused save shows the problem', async () => {
    vi.mocked(api.saveAirCalibrations).mockRejectedValue(
      new ApiError({ type: 'about:blank', title: 'Conflict', status: 409, code: 'not_eligible', detail: 'nope' } as never),
    )
    const w = mountIt(session())
    await w.get('[data-air-save="eneby"]').setValue(true)
    await w.get('[data-air-save-button]').trigger('click')
    await flushPromises()
    expect(w.get('[data-air-error]').text()).toContain('not_eligible')
  })

  it('offers the reference choice and picks it through the session', async () => {
    const store = useSyncStore()
    const s = session({ reference: null, air_calibrations: [candidate({ eligible: false, reason: 'no_reference', latency_ms: null })] })
    store.session = s
    vi.mocked(api.sessionReference).mockResolvedValue(session())
    const w = mountIt(s)
    expect(w.text()).toContain('headphones or a wired output')
    const select = w.get('[data-air-reference]')
    expect(select.findAll('option').map((o) => o.text())).toEqual(expect.arrayContaining(['Kiosk (headset)']))
    await select.setValue('kiosk')
    await flushPromises()
    expect(api.sessionReference).toHaveBeenCalledWith('s1', 'kiosk')
  })

  it('the reference picker says what to pick and that it also sets the reference for proposals', () => {
    const en_ = mountIt(session()).get('[data-air-reference-hint]').text().toLowerCase()
    expect(en_).toContain('pick a headset, wired output or another reference that plays on schedule')
    expect(en_).toContain('proposed delays')
    expect(mountIt(session(), 'ru').get('[data-air-reference-hint]').text()).toContain('наушники')
  })
})
