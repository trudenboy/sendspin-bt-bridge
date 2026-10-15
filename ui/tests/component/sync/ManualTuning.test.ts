import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import ManualTuning from '@/components/sync/ManualTuning.vue'
import { useSyncStore } from '@/stores/sync'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({ sessionDelay: vi.fn().mockResolvedValue({}), sessionSignal: vi.fn().mockResolvedValue({}), sessionSolo: vi.fn().mockResolvedValue({}), sessionAb: vi.fn().mockResolvedValue({}), writeGroupDelays: vi.fn(), getSyncGroup: vi.fn(), listProfiles: vi.fn(), captureProfile: vi.fn(), createSession: vi.fn(), endSession: vi.fn() }))
import * as api from '@/api/sync'
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'A', origin: 'bridge', adjustable: true, delay_ms: 100 }, { ma_player_id: 'b', name: 'B', origin: 'sendspin', adjustable: true, delay_ms: 0 }, { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null }]

function setup() {
  setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks()
  const store = useSyncStore()
  store.group = { id: 'g', name: 'G', session_id: 's1', members } as never
  store.session = { id: 's1', group_id: 'g', mode: 'manual', state: 'measuring', members, signal_running: false, soloed: null } as never
  return mount(ManualTuning, { global: { plugins: [i18n] } })
}

describe('ManualTuning', () => {
  afterEach(() => { vi.useRealTimers() })

  it('nudges write the clamped value live, debounced, and non-adjustable members have no controls', async () => {
    const w = setup()
    const rows = w.findAll('[data-delay-row]'); expect(rows).toHaveLength(2)
    await rows[1]!.find('[aria-label="−10 ms"]').trigger('click')          // 0 → clamped 0
    await rows[0]!.find('[aria-label="+10 ms"]').trigger('click'); await rows[0]!.find('[aria-label="+1 ms"]').trigger('click')
    vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'a', 111)
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'b', 0)
    expect(w.text()).toContain('TV')
  })

  it('plays clicks, solos and alternates A/B every four seconds', async () => {
    const w = setup()
    await w.find('[data-signal]').trigger('click'); expect(api.sessionSignal).toHaveBeenCalledWith('s1', 'start')
    await w.find('[data-solo="a"]').trigger('click'); expect(api.sessionSolo).toHaveBeenCalledWith('s1', 'a')
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    expect(api.sessionAb).toHaveBeenCalledWith('s1', 'a', 'b')
    vi.advanceTimersByTime(4000); await flushPromises(); expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', 'b')
    vi.advanceTimersByTime(4000); await flushPromises(); expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', 'a')
    await w.find('[data-solo-none]').trigger('click'); vi.advanceTimersByTime(8000); await flushPromises()
    expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', null)
  })

  it('reset returns every member to the session-start delay', async () => {
    const w = setup()
    await w.findAll('[data-delay-row]')[0]!.find('[aria-label="+10 ms"]').trigger('click'); vi.advanceTimersByTime(300)
    await w.find('[data-reset]').trigger('click'); vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 100)
  })

  it('reset writes only the members that changed', async () => {
    const w = setup()
    await w.findAll('[data-delay-row]')[0]!.find('[aria-label="+10 ms"]').trigger('click')
    vi.advanceTimersByTime(300); await flushPromises()
    vi.mocked(api.sessionDelay).mockClear()
    await w.find('[data-reset]').trigger('click'); vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenCalledTimes(1)
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'a', 100)
    expect((w.findAll('[data-delay-row]')[0]!.find('input[type="number"]').element as HTMLInputElement).value).toBe('100')
  })

  it('clamps the number field and the slider to 0–5000', async () => {
    const w = setup()
    const row = w.findAll('[data-delay-row]')[0]!
    await row.find('input[type="number"]').setValue('9000')
    vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 5000)
    await row.find('input[type="range"]').setValue('42')
    vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 42)
    await row.find('input[type="number"]').setValue('-5')
    vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 0)
  })

  it('stops the A/B alternation when the clicks stop', async () => {
    const w = setup()
    useSyncStore().session = { ...useSyncStore().session!, signal_running: true } as never
    await flushPromises()
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    await w.find('[data-signal]').trigger('click'); await flushPromises()
    expect(api.sessionSignal).toHaveBeenCalledWith('s1', 'stop')
    const calls = vi.mocked(api.sessionSolo).mock.calls.length
    vi.advanceTimersByTime(12000); await flushPromises()
    expect(vi.mocked(api.sessionSolo).mock.calls.length).toBe(calls)
  })

  it('stops the A/B alternation when a single member is soloed', async () => {
    const w = setup()
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    await w.find('[data-solo="b"]').trigger('click'); await flushPromises()
    vi.advanceTimersByTime(12000); await flushPromises()
    expect(api.sessionSolo).toHaveBeenCalledTimes(1)
    expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', 'b')
  })

  it('leaves no A/B timer behind when it unmounts', async () => {
    const w = setup()
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    w.unmount()
    vi.advanceTimersByTime(12000); await flushPromises()
    expect(api.sessionSolo).not.toHaveBeenCalled()
  })

  it('alternates between the two members chosen for A/B', async () => {
    const more = [...members.slice(0, 2), { ma_player_id: 'c', name: 'C', origin: 'bridge', adjustable: true, delay_ms: 5 }]
    setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks()
    const store = useSyncStore()
    store.group = { id: 'g', name: 'G', session_id: 's1', members: more } as never
    store.session = { id: 's1', group_id: 'g', mode: 'manual', state: 'measuring', members: more, signal_running: true, soloed: null } as never
    const w = mount(ManualTuning, { global: { plugins: [i18n] } })
    await w.find('[data-ab-b]').setValue('c')
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    expect(api.sessionAb).toHaveBeenCalledWith('s1', 'a', 'c')
    vi.advanceTimersByTime(4000); await flushPromises()
    expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', 'c')
  })

  it('marks members that left the group and gives them no controls', async () => {
    setup()
    const store = useSyncStore()
    store.session = { ...store.session!, departed: ['b'] } as never
    const w = mount(ManualTuning, { global: { plugins: [i18n] } })
    await flushPromises()
    expect(w.findAll('[data-delay-row]')).toHaveLength(1)
    const gone = w.get('[data-departed="b"]')
    expect(gone.text()).toContain('B')
    expect(gone.text()).toContain('Left the group')
    expect(gone.find('button').exists()).toBe(false)
    expect(w.find('[data-solo="b"]').exists()).toBe(false)
  })

  it('announces what is playing in a live region', async () => {
    const w = setup()
    useSyncStore().session = { ...useSyncStore().session!, signal_running: true, soloed: 'a' } as never
    await flushPromises()
    const live = w.get('[data-listen-status]')
    expect(live.attributes('aria-live')).toBe('polite')
    expect(live.text()).toContain('A')
  })

  it('shows why a listening action failed', async () => {
    const w = setup()
    vi.mocked(api.sessionSignal).mockRejectedValueOnce(Object.assign(new Error('x'), { code: 'ma_unavailable' }))
    await w.find('[data-signal]').trigger('click'); await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('That did not work')
  })
})
