import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import { useSyncStore } from '@/stores/sync'
import { SyncCaptureError } from '@/sync/capture'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

vi.mock('@/api/sync', () => ({ createSession: vi.fn(), sessionSignal: vi.fn().mockResolvedValue({}), sessionSolo: vi.fn().mockResolvedValue({}), uploadRecording: vi.fn(), sessionSkip: vi.fn().mockResolvedValue({}), sessionReference: vi.fn(), getProposal: vi.fn(), endSession: vi.fn(), getSyncGroup: vi.fn(), listProfiles: vi.fn() }))
import * as api from '@/api/sync'
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 }, { ma_player_id: 'b', name: 'Hall', origin: 'sendspin', adjustable: true, delay_ms: 0 }, { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null }]

class FakeRecorder {
  static supported = () => true; epochInvalidated = false; opened = false; recorded: number[] = []
  async open() { this.opened = true } level() { return 0.2 }
  async record(d: number) { this.recorded.push(d); return { pcm: new Int16Array(16000 * d), sampleRate: 16000, startTimeS: 20 } }
  close() {}
}

describe('MeasureWizard', () => {
  let rec: FakeRecorder
  beforeEach(() => { setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks(); rec = new FakeRecorder() })

  it('walks prepare → level → members, soloing, recording and uploading each adjustable member', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, measurements: {}, signal_running: true } as never)
    vi.mocked(api.uploadRecording).mockResolvedValueOnce({ member: 'a', status: 'ok', confidence: 0.8, clicks: 3, phase_ms: 1, spread_ms: 0.4 } as never)
                                  .mockResolvedValueOnce({ member: 'b', status: 'not_heard', reason: 'no_clicks' } as never)
    const w = mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', mode: 'semi', recorderFactory: () => rec as never }, attachTo: document.body })
    expect(w.text()).toContain('Playback will be interrupted')
    await w.find('[data-next]').trigger('click'); await flushPromises()
    expect(rec.opened).toBe(true); expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'semi', point: 'device' })
    expect(w.find('[data-level-meter]').exists()).toBe(true)
    await w.find('[data-next]').trigger('click'); await flushPromises()
    expect(w.findAll('[data-member-step]')).toHaveLength(2)                      // TV is not measured
    expect(w.find('[data-step-status]').attributes('aria-live')).toBe('polite')
    await w.find('[data-record="a"]').trigger('click'); await flushPromises()
    expect(api.sessionSolo).toHaveBeenCalledWith('s1', 'a')
    vi.advanceTimersByTime(800); await flushPromises(); await flushPromises()
    expect(rec.recorded).toEqual([6]); expect(api.uploadRecording).toHaveBeenCalledWith('s1', 'a', 20, 16000, expect.any(Int16Array))
    expect(w.find('[data-result="a"]').text()).toContain('80 %')
    await w.find('[data-record="b"]').trigger('click'); vi.advanceTimersByTime(800); await flushPromises(); await flushPromises()
    expect(w.find('[data-result="b"]').text()).toContain('Not heard')
    expect(w.find('[data-repeat="b"]').exists()).toBe(true)
    await w.find('[data-skip="b"]').trigger('click'); expect(api.sessionSkip).toHaveBeenCalledWith('s1', 'b')
    await flushPromises()
    expect(w.find('[data-result="b"]').text()).toContain('Skipped')
    expect(w.find('[data-result="a"]').text()).not.toContain('Record again')
    expect(w.find('[data-to-proposal]').attributes('disabled')).toBeUndefined()
  })

  it('restarts the round when the audio context stopped', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.createSession).mockResolvedValue({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, measurements: {}, signal_running: true } as never)
    const w = mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', mode: 'semi', recorderFactory: () => rec as never } })
    await w.find('[data-next]').trigger('click'); await flushPromises(); await w.find('[data-next]').trigger('click'); await flushPromises()
    rec.epochInvalidated = true
    await w.find('[data-record="a"]').trigger('click'); await flushPromises()
    expect(w.text()).toContain('start the measurement again'); expect(api.uploadRecording).not.toHaveBeenCalled()
  })
})

/* -- the rulings: epochs, microphone loss, lifecycle, departed members ------------------- */

/** A recorder with the real one's epoch surface, driven by the test. */
class EpochRecorder extends FakeRecorder {
  epoch = 0
  needsResume = false
  needsReopen = false
  closed = 0
  resumed = 0
  listeners = new Set<(e: number) => void>()
  nextError: SyncCaptureError | null = null
  micListeners = new Set<() => void>()
  hold: { resolve: () => void } | null = null
  holdNext = false
  resumeError: unknown = null
  onEpochInvalidated(cb: (e: number) => void) { this.listeners.add(cb); return () => this.listeners.delete(cb) }
  onMicLost(cb: () => void) { this.micListeners.add(cb); return () => this.micListeners.delete(cb) }
  loseMic() { this.needsReopen = true; for (const cb of this.micListeners) cb() }
  async record(d: number) {
    if (this.nextError) { const e = this.nextError; this.nextError = null; throw e }
    if (this.holdNext) { this.holdNext = false; await new Promise<void>((resolve) => (this.hold = { resolve })) }
    const cap = await super.record(d)
    return { ...cap, epoch: this.epoch }
  }
  async resume() { this.resumed++; if (this.resumeError) throw this.resumeError; this.needsResume = false; if (this.epochInvalidated) { this.epochInvalidated = false; this.epoch++ } }
  async open() { await super.open(); if (this.needsReopen) { this.needsReopen = false; this.epoch++ } }
  close() { this.closed++ }
  invalidate() { this.epochInvalidated = true; this.needsResume = true; for (const cb of this.listeners) cb(this.epoch) }
}

const session = (over: Record<string, unknown> = {}) =>
  ({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, departed: [], measurements: {}, signal_running: true, ...over }) as never

const ok = (member: string, confidence = 0.9) => ({ member, status: 'ok', confidence, clicks: 3, phase_ms: 1, spread_ms: 0.3, reason: '' }) as never

async function toMembers(w: ReturnType<typeof mount>) {
  await w.get('[data-next]').trigger('click'); await flushPromises()
  await w.get('[data-next]').trigger('click'); await flushPromises()
}

async function recordMember(w: ReturnType<typeof mount>, id: string, attr = 'data-record') {
  await w.get(`[${attr}="${id}"]`).trigger('click'); await flushPromises()
  await vi.advanceTimersByTimeAsync(800); await flushPromises()
}

describe('MeasureWizard rulings', () => {
  let rec: EpochRecorder
  beforeEach(() => {
    setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks(); rec = new EpochRecorder()
    useSyncStore().group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.createSession).mockResolvedValue(session())
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    vi.mocked(api.uploadRecording).mockImplementation(async (_s, member) => ok(member))
  })
  afterEach(() => { vi.useRealTimers() })

  const mountWizard = (props: Record<string, unknown> = {}) =>
    mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', mode: 'semi', recorderFactory: () => rec as never, ...props } })

  it('measures from the listening position for 12 s with a stay-put prompt', async () => {
    vi.mocked(api.createSession).mockResolvedValue(session({ point: 'position' }))
    const w = mountWizard({ mode: 'auto' })
    await w.get('input[value="position"]').setValue(true)
    await toMembers(w)
    expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'auto', point: 'position' })
    expect(w.text()).toContain('Stay where you are')
    expect(w.text()).not.toContain('Hold the phone')
    await recordMember(w, 'a')
    expect(rec.recorded).toEqual([12])
  })

  it('prompts to hold the phone near each speaker when measuring at the speakers', async () => {
    const w = mountWizard()
    await toMembers(w)
    expect(w.get('[data-member-step="a"]').text()).toContain('Hold the phone 30–50 cm from Kitchen')
    expect(w.get('[data-member-step="b"]').text()).toContain('Hold the phone 30–50 cm from Hall')
  })

  it('does not start a session when the microphone is refused', async () => {
    rec.open = vi.fn().mockRejectedValue(new SyncCaptureError('permission'))
    const w = mountWizard()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    expect(api.createSession).not.toHaveBeenCalled()
    expect(w.text()).toContain('Microphone access was denied')
    expect(w.find('[data-level-meter]').exists()).toBe(false)
  })

  it('closes the microphone when the session cannot be started', async () => {
    vi.mocked(api.createSession).mockRejectedValue(new Error('boom'))
    const w = mountWizard()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    expect(rec.closed).toBe(1)
    expect(w.text()).toContain('Could not start the measurement')
  })

  it('shows the level and says when it is too quiet or too loud', async () => {
    let level = 0.01
    rec.level = () => level
    const w = mountWizard()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    await vi.advanceTimersByTimeAsync(200)
    expect(w.get('[data-level-meter]').text()).toContain('Too quiet')
    level = 0.8
    await vi.advanceTimersByTimeAsync(200)
    expect(w.get('[data-level-meter]').text()).toContain('Too loud')
    level = 0.3
    await vi.advanceTimersByTimeAsync(200)
    expect(w.get('[data-level-meter]').text()).toContain('30 %')
    expect(w.get('[data-level-meter] [role="meter"]').attributes('aria-valuenow')).toBe('30')
  })

  it('offers the tap to continue when the context opened suspended (iOS)', async () => {
    rec.needsResume = true
    const w = mountWizard()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    expect(w.find('[data-level-meter]').exists()).toBe(true)
    await w.get('[data-resume]').trigger('click'); await flushPromises()
    expect(rec.resumed).toBe(1)
    expect(w.find('[data-resume]').exists()).toBe(false)
  })

  it('reorders members with the move buttons', async () => {
    const w = mountWizard()
    await toMembers(w)
    const order = () => w.findAll('[data-member-step]').map((e) => e.attributes('data-member-step'))
    expect(order()).toEqual(['a', 'b'])
    await w.get('[data-move-down="a"]').trigger('click')
    expect(order()).toEqual(['b', 'a'])
    await w.get('[data-move-up="a"]').trigger('click')
    expect(order()).toEqual(['a', 'b'])
    expect(w.get('[data-move-up="a"]').attributes('disabled')).toBeDefined()
  })

  it('lets a measured member be measured again and goes on once every member is done', async () => {
    const w = mountWizard()
    await toMembers(w)
    await recordMember(w, 'a')
    expect(w.find('[data-to-proposal]').attributes('disabled')).toBeDefined()
    await recordMember(w, 'a', 'data-repeat')
    expect(api.uploadRecording).toHaveBeenCalledTimes(2)
    await recordMember(w, 'b')
    await w.get('[data-to-proposal]').trigger('click')
    expect(w.emitted('proposal')).toHaveLength(1)
  })

  it('stops between members when the audio clock breaks, resumes from the tap and redoes earlier members', async () => {
    const w = mountWizard()
    await toMembers(w)
    await recordMember(w, 'a')
    expect(w.get('[data-result="a"]').text()).toContain('90 %')
    rec.invalidate(); await flushPromises()
    expect(w.text()).toContain('start the measurement again')
    expect(w.get('[data-member-step="a"]').text()).toContain('Record again')
    expect(w.get('[data-step-status]').text()).toContain('start the measurement again')
    // Recording is refused until the tap.
    await w.get('[data-record="b"]').trigger('click'); await flushPromises()
    expect(rec.recorded).toHaveLength(1)
    await w.get('[data-resume]').trigger('click'); await flushPromises()
    expect(rec.resumed).toBe(1)
    expect(w.find('[data-resume]').exists()).toBe(false)
    await recordMember(w, 'b')
    // Kitchen's measurement is from the old clock: it must be redone before going on.
    expect(w.get('[data-to-proposal]').attributes('disabled')).toBeDefined()
    await recordMember(w, 'a', 'data-repeat')
    expect(w.get('[data-member-step="a"]').text()).not.toContain('Record again')
    expect(w.get('[data-to-proposal]').attributes('disabled')).toBeUndefined()
  })

  it('never keeps captures from two clocks in one round, even without an interruption notice', async () => {
    const w = mountWizard()
    await toMembers(w)
    await recordMember(w, 'a')
    rec.epoch = 1 // a new clock the wizard was not told about
    await recordMember(w, 'b')
    expect(w.get('[data-member-step="a"]').text()).toContain('Record again')
    expect(w.get('[data-member-step="b"]').text()).not.toContain('Record again')
    expect(w.get('[data-to-proposal]').attributes('disabled')).toBeDefined()
  })

  it('keeps earlier measurements valid when a member is skipped', async () => {
    const w = mountWizard()
    await toMembers(w)
    await recordMember(w, 'a')
    await w.get('[data-skip="b"]').trigger('click'); await flushPromises()
    expect(w.get('[data-member-step="a"]').text()).not.toContain('Record again')
    expect(w.get('[data-to-proposal]').attributes('disabled')).toBeUndefined()
  })

  it('shows the recording progress over its duration and removes it when done', async () => {
    rec.holdNext = true
    const w = mountWizard()
    await toMembers(w)
    await recordMember(w, 'a')
    const bar = () => w.find('[data-member-step="a"] [role="progressbar"]')
    expect(bar().attributes('aria-valuenow')).toBe('0')
    await vi.advanceTimersByTimeAsync(3000)
    expect(bar().attributes('aria-valuenow')).toBe('50')
    expect(bar().attributes('aria-valuemax')).toBe('100')
    await vi.advanceTimersByTimeAsync(3000)
    expect(bar().attributes('aria-valuenow')).toBe('100')
    rec.hold!.resolve(); await flushPromises()
    expect(bar().exists()).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stops the progress timer and settles the step when closed mid-recording', async () => {
    rec.holdNext = true
    const w = mountWizard()
    await toMembers(w)
    await recordMember(w, 'a')
    w.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('settles a pending step when the microphone closes during the settle wait', async () => {
    const w = mountWizard()
    await toMembers(w)
    await w.get('[data-record="a"]').trigger('click'); await flushPromises()
    w.unmount(); await flushPromises()
    expect(vi.getTimerCount()).toBe(0)
    expect(rec.recorded).toEqual([])
  })

  it('does not revive an ended session with a late solo answer', async () => {
    let answer!: (s: unknown) => void
    vi.mocked(api.sessionSolo).mockImplementationOnce(() => new Promise((r) => (answer = r)) as never)
    const w = mountWizard()
    await toMembers(w)
    await w.get('[data-record="a"]').trigger('click'); await flushPromises()
    const store = useSyncStore()
    store.applyEvent(session({ state: 'cancelled' })); await flushPromises()
    answer(session({ state: 'measuring' })); await flushPromises()
    expect(store.session?.state).toBe('cancelled')
    await vi.advanceTimersByTimeAsync(1000)
    expect(rec.recorded).toEqual([])
  })

  it('keeps keyboard focus on the row button after a recording', async () => {
    const w = mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', mode: 'semi', recorderFactory: () => rec as never }, attachTo: document.body })
    await toMembers(w)
    ;(w.get('[data-record="a"]').element as HTMLElement).focus()
    await recordMember(w, 'a')
    expect(document.activeElement).toBe(w.get('[data-repeat="a"]').element)
    w.unmount()
  })

  it('says why resuming failed', async () => {
    const w = mountWizard()
    await toMembers(w)
    rec.invalidate(); await flushPromises()
    rec.resumeError = new SyncCaptureError('failed')
    await w.get('[data-resume]').trigger('click'); await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('Recording failed.')
    expect(w.get('[data-step-status]').text()).toContain('Recording failed.')
  })

  it('notices a lost microphone on the level step', async () => {
    const w = mountWizard()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    rec.loseMic(); await flushPromises()
    expect(w.find('[data-reopen]').exists()).toBe(true)
    expect(w.get('[data-next]').attributes('disabled')).toBeDefined()
  })

  it('treats a recording interrupted mid-way like a broken clock', async () => {
    const w = mountWizard()
    await toMembers(w)
    rec.nextError = new SyncCaptureError('interrupted')
    rec.needsResume = true
    await recordMember(w, 'a')
    expect(api.uploadRecording).not.toHaveBeenCalled()
    expect(w.find('[data-resume]').exists()).toBe(true)
  })

  it('offers to reopen the microphone when it was lost, and redoes earlier members', async () => {
    const w = mountWizard()
    await toMembers(w)
    await recordMember(w, 'a')
    rec.needsReopen = true
    rec.nextError = new SyncCaptureError('mic_lost')
    await recordMember(w, 'b')
    expect(w.text()).toContain('The microphone stopped')
    expect(w.get('[data-member-step="a"]').text()).toContain('Record again')
    await w.get('[data-reopen]').trigger('click'); await flushPromises()
    expect(rec.epoch).toBe(1)
    expect(w.find('[data-reopen]').exists()).toBe(false)
  })

  it('marks departed members and does not wait for them', async () => {
    vi.mocked(api.createSession).mockResolvedValue(session({ departed: ['b'] }))
    const w = mountWizard()
    await toMembers(w)
    expect(w.get('[data-member-step="b"]').text()).toContain('Left the group')
    expect(w.find('[data-record="b"]').exists()).toBe(false)
    await recordMember(w, 'a')
    expect(w.get('[data-to-proposal]').attributes('disabled')).toBeUndefined()
  })

  it('lists the members that cannot be measured', async () => {
    const w = mountWizard()
    await toMembers(w)
    expect(w.get('[data-not-measured]').text()).toContain('TV')
  })

  it('closes the microphone and ends its session when unmounted mid-measurement', async () => {
    const w = mountWizard()
    await toMembers(w)
    w.unmount()
    expect(rec.closed).toBe(1)
    expect(api.endSession).toHaveBeenCalledWith('s1')
  })

  it('closes the microphone when the session ends under it', async () => {
    const w = mountWizard()
    await toMembers(w)
    useSyncStore().applyEvent(session({ state: 'cancelled' }))
    await flushPromises()
    expect(rec.closed).toBe(1)
    expect(w.text()).toContain('The measurement session ended')
    expect(w.find('[data-member-step]').exists()).toBe(false)
  })

  it('cancels: closes the microphone, ends the session and goes back to the start', async () => {
    const w = mountWizard()
    await toMembers(w)
    await w.get('[data-cancel]').trigger('click'); await flushPromises()
    expect(rec.closed).toBe(1)
    expect(api.endSession).toHaveBeenCalledWith('s1')
    expect(w.text()).toContain('Playback will be interrupted')
    expect(w.emitted('done')).toHaveLength(1)
  })

  it('announces the step and the results in the live region', async () => {
    const w = mountWizard()
    const status = () => w.get('[data-step-status]').text()
    expect(status()).toContain('Step 1 of 5')
    await w.get('[data-next]').trigger('click'); await flushPromises()
    expect(status()).toContain('Step 2 of 5')
    await w.get('[data-next]').trigger('click'); await flushPromises()
    expect(status()).toContain('Step 3 of 5')
    await recordMember(w, 'a')
    expect(status()).toContain('Kitchen')
    expect(status()).toContain('90 %')
  })
})

describe('wizard texts', () => {
  const keys = (o: unknown, prefix = ''): string[] =>
    Object.entries(o as Record<string, unknown>).flatMap(([k, v]) =>
      typeof v === 'object' && v !== null ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`],
    )
  it('has every wizard text in English and Russian, with the agreed wording', () => {
    const enW = (en as { sync: { wizard: unknown } }).sync.wizard
    const ruW = (ru as { sync: { wizard?: unknown } }).sync.wizard
    expect(ruW).toBeDefined()
    expect(keys(ruW).sort()).toEqual(keys(enW).sort())
    const flat = JSON.stringify(enW)
    for (const s of ['Playback will be interrupted', '{pct} %', 'Not heard', 'start the measurement again']) expect(flat).toContain(s)
  })
})
