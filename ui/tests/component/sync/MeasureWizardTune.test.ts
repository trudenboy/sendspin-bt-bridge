import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import { useSyncStore } from '@/stores/sync'
import { ApiError } from '@/api/client'
import { confirmState, settleConfirm } from '@/composables/useConfirm'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({
  createSession: vi.fn(), getSession: vi.fn(), sessionSignal: vi.fn(), sessionSolo: vi.fn(), uploadRecording: vi.fn(),
  sessionSkip: vi.fn(), sessionReference: vi.fn(), getProposal: vi.fn(), computeProposal: vi.fn(), applyProposal: vi.fn(),
  finishSession: vi.fn(), endSession: vi.fn(), captureProfile: vi.fn(), endSessionOnPageHide: vi.fn(),
  sessionDelay: vi.fn(), sessionAb: vi.fn(), sessionVolume: vi.fn(), writeGroupDelays: vi.fn(), getSyncGroup: vi.fn(),
}))
import * as api from '@/api/sync'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [
  { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'b', name: 'Hall', origin: 'sendspin', adjustable: true, delay_ms: 100 },
  { ma_player_id: 'c', name: 'Porch', origin: 'sendspin', adjustable: true, delay_ms: 40 },
]
const proposal = {
  reference: 'a', predicted_spread_ms: 1, unverified: ['c'], warnings: [],
  rows: [{ member: 'a', before_ms: 0, after_ms: 0, delta_ms: 0, clamped: false }, { member: 'b', before_ms: 100, after_ms: 112, delta_ms: 12, clamped: false }],
}

class Recorder {
  epoch = 0; closed = 0; opened = 0; recorded: number[] = []
  async open() { this.opened++ } level() { return 0.2 } close() { this.closed++ }
  async record(d: number) { this.recorded.push(d); return { pcm: new Int16Array(10), sampleRate: 16000, startTimeS: 20, epoch: this.epoch } }
}

const session = (over: Record<string, unknown> = {}) =>
  ({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, departed: [], measurements: {}, history: [],
     clicker_playing: true, clicker_starts: 1, group_state: 'playing', volumes: { a: 40, b: 40, c: 40 }, muted: [], error: null,
     restore_error: null, proposal: null, reference: null, soloed: null, ...over }) as never
const tuned = (over: Record<string, unknown> = {}) =>
  session({ state: 'tuning', proposal, history: [{ spread_before_ms: 12 }],
    members: [members[0], { ...members[1], delay_ms: 112 }, members[2]], ...over })
const ok = (member: string) => ({ member, status: 'ok', confidence: 0.9, clicks: 3, phase_ms: 1, spread_ms: 0.3, reason: '' }) as never

async function recordMember(w: ReturnType<typeof mount>, id: string, attr = 'data-record') {
  await w.get(`[${attr}="${id}"]`).trigger('click'); await flushPromises()
  await vi.advanceTimersByTimeAsync(800); await flushPromises()
}

describe('MeasureWizard: the measured delays in the manual editor, finish, done', () => {
  let rec: Recorder
  beforeEach(() => {
    setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks(); rec = new Recorder()
    useSyncStore().group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.createSession).mockResolvedValue(session())
    vi.mocked(api.sessionSolo).mockImplementation(async () => session())
    vi.mocked(api.sessionSkip).mockImplementation(async () => session())
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    vi.mocked(api.uploadRecording).mockImplementation(async (_s, member) => ok(member))
    vi.mocked(api.computeProposal).mockResolvedValue(proposal as never)
    vi.mocked(api.getSession).mockResolvedValue(session({ state: 'proposed', proposal, history: [{ spread_before_ms: 12 }] }))
    vi.mocked(api.applyProposal).mockResolvedValue(tuned())
    vi.mocked(api.finishSession).mockResolvedValue(tuned({ state: 'restoring' }))
    vi.mocked(api.sessionDelay).mockImplementation(async () => tuned())
  })
  afterEach(() => { vi.useRealTimers(); if (confirmState.open) settleConfirm(false) })

  const mountWizard = () =>
    mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', recorderFactory: () => rec as never }, attachTo: document.body })

  async function toTune(w: ReturnType<typeof mount>) {
    for (let i = 0; i < 4; i++) { await w.get('[data-next]').trigger('click'); await flushPromises() }
    await recordMember(w, 'a'); await recordMember(w, 'b')
    await w.get('[data-skip="c"]').trigger('click'); await flushPromises()
    await w.get('[data-to-tune]').trigger('click'); await flushPromises()
  }

  it('computes with POST, applies the measured delays and opens the manual editor with the clicks still playing', async () => {
    const w = mountWizard()
    await toTune(w)
    expect(api.computeProposal).toHaveBeenCalledWith('s1')
    expect(api.getProposal).not.toHaveBeenCalled()
    expect(api.applyProposal).toHaveBeenCalledWith('s1', undefined)
    expect(rec.closed).toBe(1)                                         // nothing more is recorded
    expect(w.get('[data-step-status]').text()).toContain('Tune (3/4) · Listen and adjust')
    // one editing surface: the manual listening controls and delay rows, with what was measured
    expect(w.find('[data-signal]').exists()).toBe(true)
    expect(w.find('[data-solo="a"]').exists()).toBe(true)
    expect(w.get('[data-delay-row][data-member-id="b"] [data-measured]').text()).toContain('Measured: 112 ms (+12)')
    expect(w.find('[data-delay-row][data-member-id="c"] [data-measured]').exists()).toBe(false)
    expect(w.get('[data-kept]').text()).toContain('Porch')
    expect(w.get('[data-spread]').text()).toContain('12 ms measured → about 1 ms now')
  })

  it('writes hand adjustments live through the session and finishes: the delays stay, the session ends done', async () => {
    const w = mountWizard()
    await toTune(w)
    await w.get('[data-delay-row][data-member-id="b"] [aria-label="+1 ms (earlier)"]').trigger('click')
    await w.get('[data-finish]').trigger('click'); await flushPromises()
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'b', 113)    // the pending edit is written before finishing
    expect(api.finishSession).toHaveBeenCalledWith('s1')
    expect(api.endSession).not.toHaveBeenCalled()
    expect(w.get('[data-done]').text()).toContain('Restoring the group')
    useSyncStore().applyEvent(tuned({ state: 'done' }))
    await flushPromises()
    expect(w.get('[data-done]').text()).toContain('Group restored')
    expect(w.get('[data-spread]').text()).toContain('12 ms → about 1 ms')
  })

  it('finishes and saves a profile of the final delays, with the gap in its notes', async () => {
    vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p1', name: 'Evening' } as never)
    const w = mountWizard()
    await toTune(w)
    await w.get('[data-finish-save]').trigger('click'); await flushPromises()
    await w.get('[data-profile-name]').setValue('Evening')
    await w.get('[data-save-confirm]').trigger('click'); await flushPromises()
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Evening', kind: 'device', notes: 'spread 12 → 1 ms' })
    expect(vi.mocked(api.captureProfile).mock.invocationCallOrder[0]!).toBeLessThan(vi.mocked(api.finishSession).mock.invocationCallOrder[0]!)
    expect(w.get('[data-saved]').text()).toContain('Evening')
  })

  it('cancel in the editor asks, puts back the delays from before the measurement, then ends', async () => {
    const w = mountWizard()
    await toTune(w)
    useSyncStore().applyEvent(tuned())
    await w.get('[data-tune] [data-cancel]').trigger('click'); await flushPromises()
    expect(confirmState.options?.title).toBe('Stop and put the delays back?')
    settleConfirm(true); await flushPromises()
    await vi.advanceTimersByTimeAsync(300); await flushPromises()
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'b', 100)
    expect(api.sessionDelay).not.toHaveBeenCalledWith('s1', 'a', expect.anything())   // unchanged
    expect(api.endSession).toHaveBeenCalledWith('s1')
    expect(w.get('[data-notice]').text()).toContain('Measurement cancelled')
  })

  it('cancel with recordings asks first; "Keep going" keeps everything', async () => {
    const w = mountWizard()
    for (let i = 0; i < 4; i++) { await w.get('[data-next]').trigger('click'); await flushPromises() }
    await recordMember(w, 'a')
    await w.get('[data-cancel]').trigger('click'); await flushPromises()
    expect(confirmState.options?.title).toBe('Stop measuring?')
    settleConfirm(false); await flushPromises()
    expect(api.endSession).not.toHaveBeenCalled()
    expect(w.find('[data-result="a"]').exists()).toBe(true)
  })

  it('shows the values to copy when Music Assistant refuses the write, and retries', async () => {
    vi.mocked(api.applyProposal).mockRejectedValueOnce(new ApiError({ status: 403, code: 'ma_forbidden' }))
    const w = mountWizard()
    await toTune(w)
    expect(w.get('[data-copy-text]').text()).toContain('Hall: 112 ms')
    await w.get('[data-retry-proposal]').trigger('click'); await flushPromises()
    expect(api.computeProposal).toHaveBeenCalledTimes(1)               // the proposal is kept, only the write is retried
    expect(api.applyProposal).toHaveBeenCalledTimes(2)
    expect(w.find('[data-tune]').exists()).toBe(true)
  })

  it('says why the delays could not be worked out and retries', async () => {
    vi.mocked(api.computeProposal).mockRejectedValueOnce(new ApiError({ status: 409, code: 'no_measurement' }))
    const w = mountWizard()
    await toTune(w)
    expect(w.get('[data-proposal-error]').text()).toContain('no_measurement')
    await w.get('[data-retry-proposal]').trigger('click'); await flushPromises()
    expect(w.find('[data-tune]').exists()).toBe(true)
  })

  it('leaves out a member that left the group when it applies', async () => {
    vi.mocked(api.uploadRecording).mockImplementation(async (_s, member) => {
      if (member === 'b') throw new ApiError({ status: 409, code: 'member_gone' })
      return ok(member)
    })
    vi.mocked(api.getSession).mockResolvedValue(session({ departed: ['b'] }))
    vi.mocked(api.computeProposal).mockResolvedValue(proposal as never)
    const w = mountWizard()
    await toTune(w)
    expect(api.applyProposal).toHaveBeenCalledWith('s1', { a: 0 })
  })

  it('a session finished on another device after the apply shows done, never "ended"', async () => {
    const w = mountWizard()
    await toTune(w)
    useSyncStore().applyEvent(tuned({ state: 'restoring' }))
    await flushPromises()
    expect(w.find('[data-done]').exists()).toBe(true)
    expect(w.text()).not.toContain('The measurement ended')
  })

  it('plays a quick listen on the whole group for 10 s once the group is restored, never stopping music', async () => {
    const w = mountWizard()
    await toTune(w)
    await w.get('[data-finish]').trigger('click'); await flushPromises()
    useSyncStore().applyEvent(tuned({ state: 'done' })); await flushPromises()
    vi.mocked(api.createSession).mockResolvedValue(session({ id: 'q1', mode: 'manual', clicker_playing: false }))
    vi.mocked(api.sessionSignal).mockResolvedValue(session({ id: 'q1', mode: 'manual' }))
    await w.get('[data-listen]').trigger('click'); await flushPromises()
    expect(api.createSession).toHaveBeenLastCalledWith({ group_id: 'g', mode: 'manual' })
    useSyncStore().applyEvent(session({ id: 'q1', mode: 'manual', state: 'tuning' }))
    expect(useSyncStore().session?.id).toBe('s1')
    await vi.advanceTimersByTimeAsync(10_000); await flushPromises()
    expect(api.sessionSignal).not.toHaveBeenCalledWith('q1', 'stop')
    expect(api.endSession).toHaveBeenCalledWith('q1')
  })

  it('skips the speakers chosen on step 1 as soon as the measurement starts', async () => {
    const w = mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', preSkip: ['c'], recorderFactory: () => rec as never } })
    await w.get('[data-next]').trigger('click'); await flushPromises()
    expect(api.sessionSkip).toHaveBeenCalledWith('s1', 'c')
    for (let i = 0; i < 3; i++) { await w.get('[data-next]').trigger('click'); await flushPromises() }
    expect(w.get('[data-result="c"]').text()).toContain('Skipped')
  })
})
