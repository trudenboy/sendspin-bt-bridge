import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import ManualTuning from '@/components/sync/ManualTuning.vue'
import { useSyncStore } from '@/stores/sync'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({ sessionDelay: vi.fn().mockResolvedValue({}), sessionSignal: vi.fn().mockResolvedValue({}), sessionVolume: vi.fn().mockResolvedValue({}), sessionSolo: vi.fn().mockResolvedValue({}), sessionAb: vi.fn().mockResolvedValue({}), sessionSound: vi.fn().mockResolvedValue({}), writeGroupDelays: vi.fn(), getSyncGroup: vi.fn(), listProfiles: vi.fn(), captureProfile: vi.fn(), createSession: vi.fn(), endSession: vi.fn() }))
import * as api from '@/api/sync'
import { ApiError } from '@/api/client'
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'A', origin: 'bridge', adjustable: true, delay_ms: 100 }, { ma_player_id: 'b', name: 'B', origin: 'sendspin', adjustable: true, delay_ms: 0 }, { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null }]

/** The clicker plays on the group: solo and A/B are only offered then. */
async function running() {
  const store = useSyncStore()
  store.session = { ...store.session!, clicker_playing: true, clicker_starts: 1, group_state: 'playing' } as never
  await flushPromises()
}

const IDLE = { clicker_playing: false, clicker_starts: 0, group_state: 'idle', volumes: { a: 40, b: 30, cast: null }, muted: [] }

/** The manual tab with its preparation skipped (the tests of the preparation mount it as it opens). */
function setup({ prepared = true, over = {} as Record<string, unknown>, props = {} as Record<string, unknown> } = {}) {
  setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks()
  const store = useSyncStore()
  store.group = { id: 'g', name: 'G', session_id: 's1', members } as never
  store.session = { id: 's1', group_id: 'g', mode: 'manual', state: 'tuning', members, soloed: null, ...IDLE, ...over } as never
  const w = mount(ManualTuning, { global: { plugins: [i18n] }, props })
  if (prepared) void w.get('[data-manual-prep] [data-cancel]').trigger('click')
  return w
}

describe('ManualTuning', () => {
  afterEach(() => { vi.useRealTimers() })

  it('nudges write the clamped value live, debounced, and non-adjustable members have no controls', async () => {
    const w = setup()
    const rows = w.findAll('[data-delay-row]'); expect(rows).toHaveLength(2)
    await rows[1]!.find('[aria-label="−10 ms (later)"]').trigger('click')          // 0 → clamped 0
    await rows[0]!.find('[aria-label="+10 ms (earlier)"]').trigger('click'); await rows[0]!.find('[aria-label="+1 ms (earlier)"]').trigger('click')
    vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'a', 111)
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'b', 0)
    expect(w.text()).toContain('TV')
  })

  it('plays clicks, solos and alternates A/B every four seconds', async () => {
    const w = setup()
    await flushPromises()
    await w.find('[data-signal]').trigger('click'); expect(api.sessionSignal).toHaveBeenCalledWith('s1', 'start')
    await running()  // the bridge sees the clicker playing
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
    await w.findAll('[data-delay-row]')[0]!.find('[aria-label="+10 ms (earlier)"]').trigger('click'); vi.advanceTimersByTime(300)
    await w.find('[data-reset]').trigger('click'); vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 100)
  })

  it('reset writes only the members that changed', async () => {
    const w = setup()
    await w.findAll('[data-delay-row]')[0]!.find('[aria-label="+10 ms (earlier)"]').trigger('click')
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
    await running()
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    await w.find('[data-signal]').trigger('click'); await flushPromises()
    expect(api.sessionSignal).toHaveBeenCalledWith('s1', 'stop_clicker')  // only our clicker, never someone's music
    const calls = vi.mocked(api.sessionSolo).mock.calls.length
    vi.advanceTimersByTime(12000); await flushPromises()
    expect(vi.mocked(api.sessionSolo).mock.calls.length).toBe(calls)
  })

  it('stops the A/B alternation when a single member is soloed', async () => {
    const w = setup()
    await running()
    await w.find('[data-ab]').trigger('click'); await flushPromises()
    await w.find('[data-solo="b"]').trigger('click'); await flushPromises()
    vi.advanceTimersByTime(12000); await flushPromises()
    expect(api.sessionSolo).toHaveBeenCalledTimes(1)
    expect(api.sessionSolo).toHaveBeenLastCalledWith('s1', 'b')
  })

  it('leaves no A/B timer behind when it unmounts', async () => {
    const w = setup()
    await running()
    expect(w.get('[data-ab]').attributes('disabled')).toBeUndefined()
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
    store.session = { id: 's1', group_id: 'g', mode: 'manual', state: 'measuring', members: more, ...IDLE, clicker_playing: true, soloed: null } as never
    const w = mount(ManualTuning, { global: { plugins: [i18n] } })  // the clicker plays: straight to tuning
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
    await w.get('[data-manual-prep] [data-cancel]').trigger('click')
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
    useSyncStore().session = { ...useSyncStore().session!, clicker_playing: true, soloed: 'a' } as never
    await flushPromises()
    const live = w.get('[data-listen-status]')
    expect(live.attributes('aria-live')).toBe('polite')
    expect(live.text()).toContain('A')
  })

  it('shows why a listening action failed', async () => {
    const w = setup()
    await flushPromises()
    vi.mocked(api.sessionSignal).mockRejectedValueOnce(Object.assign(new Error('x'), { code: 'ma_unavailable' }))
    await w.find('[data-signal]').trigger('click'); await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('That did not work')
  })

  it('shows a refused write as a permission problem and puts the real value back', async () => {
    const w = setup()
    vi.mocked(api.sessionDelay).mockRejectedValueOnce(new ApiError({ status: 403, code: 'ma_forbidden' }))
    vi.mocked(api.getSyncGroup).mockResolvedValue({ id: 'g', name: 'G', session_id: 's1', members: structuredClone(members) } as never)
    const row = () => w.findAll('[data-delay-row]')[0]!
    await row().find('[aria-label="+10 ms (earlier)"]').trigger('click')
    expect((row().find('input[type="number"]').element as HTMLInputElement).value).toBe('110')
    await vi.advanceTimersByTimeAsync(300); await flushPromises()
    const alert = w.get('[data-write-error]')
    expect(alert.attributes('role')).toBe('alert')
    expect(alert.text()).toContain('permission')
    expect(alert.text()).toContain('A')
    expect((row().find('input[type="number"]').element as HTMLInputElement).value).toBe('100')
    expect(w.get('[data-reset]').attributes('disabled')).toBeDefined()
  })

  it('shows other failed writes with their code', async () => {
    const w = setup()
    vi.mocked(api.sessionDelay).mockRejectedValueOnce(new ApiError({ status: 502, code: 'ma_unavailable' }))
    vi.mocked(api.getSyncGroup).mockResolvedValue({ id: 'g', name: 'G', session_id: 's1', members: structuredClone(members) } as never)
    await w.findAll('[data-delay-row]')[0]!.find('[aria-label="+1 ms (earlier)"]').trigger('click')
    await vi.advanceTimersByTimeAsync(300); await flushPromises()
    expect(w.get('[data-write-error]').text()).toContain('ma_unavailable')
  })

  it('follows delays from sync events unless a local write is pending', async () => {
    const w = setup()
    const store = useSyncStore()
    const value = (i: number) => (w.findAll('[data-delay-row]')[i]!.find('input[type="number"]').element as HTMLInputElement).value
    store.applyEvent({ ...store.session!, members: [{ ...members[0]!, delay_ms: 150 }, { ...members[1]!, delay_ms: 30 }, members[2]!] } as never)
    await flushPromises()
    expect(value(0)).toBe('150')
    expect(value(1)).toBe('30')
    await w.findAll('[data-delay-row]')[0]!.find('[aria-label="+1 ms (earlier)"]').trigger('click')
    store.applyEvent({ ...store.session!, members: [{ ...members[0]!, delay_ms: 400 }, { ...members[1]!, delay_ms: 40 }, members[2]!] } as never)
    await flushPromises()
    expect(value(0)).toBe('151')
    expect(value(1)).toBe('40')
  })

  it('offers solo and A/B only while the clicks play', async () => {
    const w = setup()
    await flushPromises()
    expect(w.get('[data-solo="a"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-ab]').attributes('disabled')).toBeDefined()
    await w.find('[data-signal]').trigger('click')
    await running()  // offered once the bridge sees the clicker playing
    expect(w.get('[data-solo="a"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-ab]').attributes('disabled')).toBeUndefined()
  })

  it('takes the solo controls away again when starting the clicks fails', async () => {
    const w = setup()
    await flushPromises()
    vi.mocked(api.sessionSignal).mockRejectedValueOnce(new ApiError({ status: 502, code: 'ma_unavailable' }))
    await w.find('[data-signal]').trigger('click'); await flushPromises()
    expect(w.get('[data-solo="a"]').attributes('disabled')).toBeDefined()
  })

  it('groups each member\'s nudge buttons under the member\'s name', () => {
    const w = setup()
    const group = w.findAll('[data-delay-row]')[0]!.get('[role="group"]')
    expect(group.attributes('aria-label')).toContain('A')
    expect(group.findAll('button')).toHaveLength(4)
  })

  it('offers solo only once the bridge sees the clicker playing', async () => {
    const w = setup()
    await flushPromises()
    await w.find('[data-signal]').trigger('click'); await flushPromises()
    expect(w.get('[data-solo="a"]').attributes('disabled')).toBeDefined()
    await running()
    expect(w.get('[data-solo="a"]').attributes('disabled')).toBeUndefined()
  })

  it('explains the clicker, and says when it stopped', async () => {
    const w = setup()
    await running()
    expect(w.text()).toContain('Stop clicks stops only the click track.')
    useSyncStore().session = { ...useSyncStore().session!, clicker_playing: false, group_state: 'idle' } as never
    await flushPromises()
    expect(w.get('[data-listen-status]').text()).toContain('Clicks stopped.')
    expect(w.text()).not.toContain('Music returns')
  })

  it('names a member that cannot be muted when a solo is refused', async () => {
    const w = setup()
    await running()
    vi.mocked(api.sessionSolo).mockRejectedValueOnce(new ApiError({ status: 409, code: 'sync_mute_unsupported', member: 'cast' }))
    await w.find('[data-solo="a"]').trigger('click'); await flushPromises()
    expect(w.get('[data-listen-error]').text()).toContain('TV cannot be muted')
  })
})

describe('ManualTuning: getting the group ready', () => {
  afterEach(() => { vi.useRealTimers() })

  it('stops the group, starts the clicker and sets the volumes before tuning', async () => {
    const w = setup({ prepared: false, over: { group_state: 'playing' } })
    const store = useSyncStore()
    await flushPromises()
    expect(w.find('[data-signal]').exists()).toBe(false)  // no tuning controls yet
    expect(w.get('[data-group-state]').text()).toContain('Playing')
    expect(w.get('[data-manual-prep] [data-next]').attributes('disabled')).toBeDefined()
    vi.mocked(api.sessionSignal).mockResolvedValueOnce({ ...store.session!, group_state: 'idle' } as never)
    await w.get('[data-stop-group]').trigger('click'); await flushPromises()
    expect(api.sessionSignal).toHaveBeenCalledWith('s1', 'stop')  // "Stop group" stops the user's music on purpose
    expect(w.get('[data-manual-prep] [data-cancel]').text()).toBe('Skip')
    await w.get('[data-manual-prep] [data-next]').trigger('click'); await flushPromises()
    vi.mocked(api.sessionSignal).mockResolvedValueOnce({ ...store.session!, clicker_playing: true, clicker_starts: 1, group_state: 'playing' } as never)
    await w.get('[data-start-clicker]').trigger('click'); await flushPromises()
    expect(api.sessionSignal).toHaveBeenLastCalledWith('s1', 'start')
    await w.get('[data-manual-prep] [data-next]').trigger('click'); await flushPromises()
    expect(w.text()).toContain('Set a level where the clicks are clearly audible where you measure.')
    w.findComponent({ name: 'VolumeSlider' }).vm.$emit('update:volume', 70)
    await flushPromises()
    expect(api.sessionVolume).toHaveBeenCalledWith('s1', 'a', 70)
    await w.get('[data-manual-prep] [data-next]').trigger('click'); await flushPromises()
    expect(w.find('[data-manual-prep]').exists()).toBe(false)
    expect(w.get('[data-solo="a"]').attributes('disabled')).toBeUndefined()
  })

  it('opens straight on tuning when the clicker already plays (a reload)', () => {
    setActivePinia(createPinia()); vi.clearAllMocks()
    const store = useSyncStore()
    store.group = { id: 'g', name: 'G', session_id: 's1', members } as never
    store.session = { id: 's1', group_id: 'g', mode: 'manual', state: 'tuning', members, soloed: null, ...IDLE, clicker_playing: true } as never
    const w = mount(ManualTuning, { global: { plugins: [i18n] } })
    expect(w.find('[data-manual-prep]').exists()).toBe(false)
    expect(w.find('[data-signal]').exists()).toBe(true)
  })
})

describe('ManualTuning: UX round', () => {
  afterEach(() => { vi.useRealTimers() })

  it('skips the stop step when nothing plays and says so', async () => {
    const w = setup({ prepared: false })   // IDLE: the group is stopped
    expect(w.find('[data-stop-group]').exists()).toBe(false)
    expect(w.get('[data-start-clicker]').exists()).toBe(true)
    expect(w.get('[data-auto-note]').text()).toBe('The group is not playing — moving on.')
    expect(w.get('[data-manual-prep] h2').text()).toContain('(2/3)')
  })

  it('says which way the delay moves next to every delay control', () => {
    const w = setup()
    for (const row of w.findAll('[data-delay-row]')) expect(row.get('[data-sign-hint]').text()).toBe('+ plays earlier · − plays later')
  })

  it('shows the measured value per member with a reset to it, and keeps members that were not measured', async () => {
    const w = setup({ prepared: false, props: { skipPrep: true, measured: { a: 130 }, before: { a: 100, b: 0 } }, over: { state: 'tuning', mode: 'semi',
      members: [{ ...members[0], delay_ms: 130 }, members[1], members[2]], clicker_playing: true } })
    await flushPromises()
    const [rowA, rowB] = w.findAll('[data-delay-row]')
    expect(rowA!.get('[data-measured]').text()).toContain('Measured: 130 ms (+30)')
    expect(rowB!.find('[data-measured]').exists()).toBe(false)           // not heard or skipped: no measured value
    expect(rowA!.find('[data-reset-measured]').exists()).toBe(false)     // already at the measured value
    await rowA!.find('[aria-label="+10 ms (earlier)"]').trigger('click'); vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 140)
    await rowA!.get('[data-reset-measured="a"]').trigger('click'); vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 130)
    // "Put back" goes to the delays from before the measurement, not to the measured ones
    await w.get('[data-reset]').trigger('click'); vi.advanceTimersByTime(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenLastCalledWith('s1', 'a', 100)
  })
})

describe('ManualTuning: the listening sound', () => {
  afterEach(() => { vi.useRealTimers() })

  it('offers clicks, clicks with accent and noise, with a hint for the one picked', async () => {
    const w = setup({ over: { sound: 'clicks' } })
    await flushPromises()
    const options = w.findAll('[data-sound] [role="radio"]')
    expect(options.map((o) => o.text())).toEqual(['Clicks', 'Clicks with accent', 'Noise'])
    expect(w.get('[data-sound-option="clicks"]').attributes('aria-checked')).toBe('true')
    expect(w.get('[data-sound-hint]').text()).toBe(en.sync.sound.hint.clicks)
    vi.mocked(api.sessionSound).mockResolvedValueOnce({ ...useSyncStore().session!, sound: 'noise' } as never)
    await w.get('[data-sound-option="noise"]').trigger('click')
    expect(w.get('[data-sound-option="noise"]').attributes('aria-checked')).toBe('true')
    expect(w.get('[data-sound-hint]').text()).toBe(en.sync.sound.hint.noise)
    expect(api.sessionSound).not.toHaveBeenCalled()  // applied once the choice settles
    vi.advanceTimersByTime(400); await flushPromises()
    expect(api.sessionSound).toHaveBeenCalledWith('s1', 'noise')
    expect(useSyncStore().session?.sound).toBe('noise')
    expect(w.get('[data-sound-option="noise"]').attributes('aria-checked')).toBe('true')
  })

  it('arrow keys move the choice, and only the one it settles on restarts the clicks', async () => {
    const w = setup({ over: { sound: 'clicks' } })
    await flushPromises()
    await w.get('[data-sound-option="clicks"]').trigger('keydown', { key: 'ArrowRight' })
    vi.advanceTimersByTime(200)
    await w.get('[data-sound-option="accent"]').trigger('keydown', { key: 'ArrowRight' })
    expect(w.get('[data-sound-option="noise"]').attributes('aria-checked')).toBe('true')
    vi.advanceTimersByTime(400); await flushPromises()
    expect(api.sessionSound).toHaveBeenCalledTimes(1)
    expect(api.sessionSound).toHaveBeenCalledWith('s1', 'noise')
  })

  it('going back to the playing sound before it settles sends nothing', async () => {
    const w = setup({ over: { sound: 'clicks' } })
    await flushPromises()
    await w.get('[data-sound-option="noise"]').trigger('click')
    await w.get('[data-sound-option="clicks"]').trigger('click')
    vi.advanceTimersByTime(400); await flushPromises()
    expect(api.sessionSound).not.toHaveBeenCalled()
  })

  it('a sound the bridge refused goes back to the one it plays, and says so', async () => {
    const w = setup({ over: { sound: 'clicks' } })
    await flushPromises()
    vi.mocked(api.sessionSound).mockRejectedValueOnce(new ApiError({ status: 409, code: 'session_state' }))
    await w.get('[data-sound-option="accent"]').trigger('click'); vi.advanceTimersByTime(400); await flushPromises()
    expect(w.get('[data-sound-option="clicks"]').attributes('aria-checked')).toBe('true')
    expect(w.get('[data-listen-error]').text()).toContain('session_state')
  })

  it('the editor after a semi-automatic apply has the sounds too; the measurement has none', async () => {
    const tuned = setup({ over: { mode: 'semi', state: 'tuning', sound: 'clicks' } })
    await flushPromises()
    expect(tuned.find('[data-sound]').exists()).toBe(true)
    const measuring = setup({ over: { mode: 'semi', state: 'proposed', sound: null } })
    await flushPromises()
    expect(measuring.find('[data-sound]').exists()).toBe(false)
  })
})
