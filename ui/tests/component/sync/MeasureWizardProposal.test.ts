import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import { useSyncStore } from '@/stores/sync'
import { ApiError } from '@/api/client'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({
  createSession: vi.fn(), getSession: vi.fn(), sessionSignal: vi.fn(), sessionSolo: vi.fn(), uploadRecording: vi.fn(),
  sessionSkip: vi.fn(), sessionReference: vi.fn(), getProposal: vi.fn(), computeProposal: vi.fn(), applyProposal: vi.fn(),
  undoApply: vi.fn(), verifySession: vi.fn(), endSession: vi.fn(), captureProfile: vi.fn(), endSessionOnPageHide: vi.fn(),
}))
import * as api from '@/api/sync'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [
  { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'b', name: 'Hall', origin: 'sendspin', adjustable: true, delay_ms: 100 },
]
const proposal = {
  reference: 'a', predicted_spread_ms: 1, unverified: [], warnings: [],
  rows: [{ member: 'a', before_ms: 0, after_ms: 0, delta_ms: 0, clamped: false }, { member: 'b', before_ms: 100, after_ms: 112, delta_ms: 12, clamped: false }],
}

class Recorder {
  epoch = 0; closed = 0; opened = 0; recorded: number[] = []
  async open() { this.opened++ } level() { return 0.2 } close() { this.closed++ }
  async record(d: number) { this.recorded.push(d); return { pcm: new Int16Array(10), sampleRate: 16000, startTimeS: 20, epoch: this.epoch } }
}

const session = (over: Record<string, unknown> = {}) =>
  ({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, departed: [], measurements: {}, history: [],
     signal_running: true, error: null, restore_error: null, proposal: null, reference: null, round: 1, soloed: null, undo_until_s: null, ...over }) as never
const ok = (member: string) => ({ member, status: 'ok', confidence: 0.9, clicks: 3, phase_ms: 1, spread_ms: 0.3, reason: '' }) as never

async function recordMember(w: ReturnType<typeof mount>, id: string, attr = 'data-record') {
  await w.get(`[${attr}="${id}"]`).trigger('click'); await flushPromises()
  await vi.advanceTimersByTimeAsync(800); await flushPromises()
}

describe('MeasureWizard: proposal, apply, verification, done', () => {
  let rec: Recorder
  beforeEach(() => {
    setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks(); rec = new Recorder()
    useSyncStore().group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.createSession).mockResolvedValue(session())
    vi.mocked(api.sessionSolo).mockImplementation(async () => session())
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    vi.mocked(api.uploadRecording).mockImplementation(async (_s, member) => ok(member))
    vi.mocked(api.computeProposal).mockResolvedValue(proposal as never)
    vi.mocked(api.getSession).mockResolvedValue(session({ state: 'proposed', proposal, history: [{ round: 1, spread_before_ms: 12 }] }))
  })
  afterEach(() => { vi.useRealTimers() })

  const mountWizard = (mode: 'semi' | 'auto' = 'semi') =>
    mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', mode, recorderFactory: () => rec as never } })

  async function toProposal(w: ReturnType<typeof mount>) {
    await w.get('[data-next]').trigger('click'); await flushPromises()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    await recordMember(w, 'a'); await recordMember(w, 'b')
    await w.get('[data-to-proposal]').trigger('click'); await flushPromises()
  }

  it('computes the proposal with POST (never a GET), shows it and applies it; the group is then restored', async () => {
    const w = mountWizard()
    await toProposal(w)
    expect(api.computeProposal).toHaveBeenCalledWith('s1')
    expect(api.getProposal).not.toHaveBeenCalled()
    expect(w.get('[data-row="b"]').text()).toContain('+12')
    expect(rec.closed).toBe(1)                                   // closed on the proposal step
    vi.mocked(api.applyProposal).mockResolvedValue(session({ state: 'restoring', history: [{ round: 1, spread_before_ms: 12 }] }))
    await w.get('[data-apply]').trigger('click'); await flushPromises()
    expect(api.applyProposal).toHaveBeenCalledWith('s1', undefined, false)
    expect(rec.closed).toBe(1)                                   // and it stays closed
    expect(w.get('[data-done]').text()).toContain('Restoring the group')
    useSyncStore().applyEvent(session({ state: 'done', history: [{ round: 1, spread_before_ms: 12 }] }))
    await flushPromises()
    expect(w.get('[data-done]').text()).toContain('Group restored')
    expect(w.text()).not.toContain('The measurement session ended')
    expect(w.get('[data-step-status]').text()).toContain('Group restored')
    expect(w.get('h2').text()).toContain('Step 5 of 5')
  })

  it('runs an optional check round in semi-automatic mode and shows the spread after it', async () => {
    const w = mountWizard()
    await toProposal(w)
    vi.mocked(api.applyProposal).mockResolvedValue(session({ state: 'verifying', round: 2, history: [{ round: 1, spread_before_ms: 12 }] }))
    await w.get('[data-check-again]').setValue(true)
    await w.get('[data-apply]').trigger('click'); await flushPromises()
    expect(api.applyProposal).toHaveBeenCalledWith('s1', undefined, true)
    expect(rec.opened).toBe(2)                                   // reopened for the check round
    expect(w.get('h2').text()).toContain('Check round')
    expect(w.find('[data-result="a"]').exists()).toBe(false)     // a fresh round
    await recordMember(w, 'a'); await recordMember(w, 'b')
    vi.mocked(api.getSession).mockResolvedValue(session({ state: 'restoring', round: 2, history: [{ round: 1, spread_before_ms: 12, spread_after_ms: 2 }] }))
    await w.get('[data-to-proposal]').trigger('click'); await flushPromises()
    expect(api.computeProposal).toHaveBeenCalledTimes(2)
    expect(w.get('[data-done]').text()).toContain('12 ms')
    expect(w.get('[data-done]').text()).toContain('2 ms')
  })

  it('applies by itself in automatic mode and offers Undo counting down from the bridge’s window', async () => {
    vi.mocked(api.createSession).mockResolvedValue(session({ mode: 'auto' }))
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'proposed', proposal }))
    vi.mocked(api.applyProposal).mockResolvedValue(session({ mode: 'auto', state: 'verifying', round: 2, undo_until_s: 12 }))
    const w = mountWizard('auto')
    await toProposal(w)
    expect(api.applyProposal).toHaveBeenCalledWith('s1', undefined, undefined)
    expect(w.get('h2').text()).toContain('Check round')
    expect(w.get('[data-undo]').text()).toContain('12 s')
    await vi.advanceTimersByTimeAsync(1000)
    expect(w.get('[data-undo]').text()).toContain('11 s')
    vi.mocked(api.undoApply).mockResolvedValue(session({ mode: 'auto', state: 'restoring', round: 2, undo_until_s: null }))
    await w.get('[data-undo]').trigger('click'); await flushPromises()
    expect(api.undoApply).toHaveBeenCalledWith('s1')
    expect(w.get('[data-done]').text()).toContain('back to where they were')
    expect(rec.closed).toBe(2)                                   // proposal step, then done
  })

  it('disables Undo once the window is over', async () => {
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'proposed', proposal }))
    vi.mocked(api.applyProposal).mockResolvedValue(session({ mode: 'auto', state: 'verifying', round: 2, undo_until_s: 3 }))
    const w = mountWizard('auto')
    await toProposal(w)
    await vi.advanceTimersByTimeAsync(3000)
    expect(w.get('[data-undo]').attributes('disabled')).toBeDefined()
    await w.get('[data-undo]').trigger('click')
    expect(api.undoApply).not.toHaveBeenCalled()
  })

  it('follows a refinement the bridge applied itself after the check round (automatic)', async () => {
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'proposed', proposal }))
    vi.mocked(api.applyProposal).mockResolvedValue(session({ mode: 'auto', state: 'verifying', round: 2, undo_until_s: 30 }))
    const w = mountWizard('auto')
    await toProposal(w)
    await recordMember(w, 'a'); await recordMember(w, 'b')
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'verifying', round: 3, undo_until_s: 30 }))
    await w.get('[data-to-proposal]').trigger('click'); await flushPromises()
    expect(api.applyProposal).toHaveBeenCalledTimes(1)          // the bridge applied round 2
    expect(w.get('h2').text()).toContain('Check round')
    expect(w.find('[data-result="a"]').exists()).toBe(false)
    expect(w.get('[data-step-status]').text()).toContain('refinement')
  })

  it('shows the values to copy when Music Assistant refuses the automatic write', async () => {
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'proposed', proposal }))
    vi.mocked(api.applyProposal).mockRejectedValue(new ApiError({ status: 403, code: 'ma_forbidden' }))
    const w = mountWizard('auto')
    await toProposal(w)
    expect(w.text()).toContain('administrator')
    expect(w.get('[data-copy-text]').text()).toContain('Kitchen: 0 ms\nHall: 112 ms')
    expect(rec.closed).toBe(1)
  })

  it('applies and saves a profile with the spread in its notes', async () => {
    vi.mocked(api.applyProposal).mockResolvedValue(session({ state: 'restoring' }))
    vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p1', name: 'Evening' } as never)
    const w = mountWizard()
    await toProposal(w)
    w.getComponent({ name: 'StepProposal' }).vm.$emit('applyAndSave', 'Evening', undefined, false)
    await flushPromises()
    expect(api.applyProposal).toHaveBeenCalledWith('s1', undefined, false)
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Evening', kind: 'device', notes: 'spread 12 → 1 ms' })
    expect(w.get('[data-done]').text()).toContain('Evening')
  })

  it('offers no reference choice (the normalised proposal does not depend on it)', async () => {
    const w = mountWizard()
    await toProposal(w)
    expect(w.find('[data-reference]').exists()).toBe(false)
    expect(api.sessionReference).not.toHaveBeenCalled()
  })

  it('saves the profile even when the done event arrived before the apply answer', async () => {
    vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p1', name: 'Evening' } as never)
    const w = mountWizard()
    await toProposal(w)
    vi.mocked(api.applyProposal).mockImplementation(async () => {
      useSyncStore().applyEvent(session({ state: 'done' }))
      await flushPromises()
      return session({ state: 'done' })
    })
    w.getComponent({ name: 'StepProposal' }).vm.$emit('applyAndSave', 'Evening', undefined, false)
    await flushPromises()
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Evening', kind: 'device', notes: 'spread 12 → 1 ms' })
    expect(w.get('[data-done]').text()).toContain('Evening')
  })

  it('offers Undo on the proposal when the bridge could not write its refinement within the undo window', async () => {
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'proposed', proposal }))
    vi.mocked(api.applyProposal).mockResolvedValue(session({ mode: 'auto', state: 'verifying', round: 2, undo_until_s: 30 }))
    const w = mountWizard('auto')
    await toProposal(w)
    await recordMember(w, 'a'); await recordMember(w, 'b')
    vi.mocked(api.computeProposal).mockRejectedValueOnce(new ApiError({ status: 403, code: 'ma_forbidden' }))
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'proposed', round: 2, proposal, undo_until_s: 20 }))
    await w.get('[data-to-proposal]').trigger('click'); await flushPromises()
    expect(w.text()).toContain('administrator')
    expect(w.get('[data-undo]').attributes('disabled')).toBeUndefined()
    vi.mocked(api.undoApply).mockResolvedValue(session({ mode: 'auto', state: 'proposed', round: 2, undo_until_s: null }))
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    await w.get('[data-undo]').trigger('click'); await flushPromises()
    expect(api.undoApply).toHaveBeenCalledWith('s1')
    expect(api.endSession).toHaveBeenCalledWith('s1')           // proposed stays live after an undo: end it
    expect(w.find('[data-done]').exists()).toBe(true)
  })

  it('an undo during a check-round recording drops that recording', async () => {
    vi.mocked(api.getSession).mockResolvedValue(session({ mode: 'auto', state: 'proposed', proposal }))
    vi.mocked(api.applyProposal).mockResolvedValue(session({ mode: 'auto', state: 'verifying', round: 2, undo_until_s: 30 }))
    const w = mountWizard('auto')
    await toProposal(w)
    let release!: () => void
    vi.mocked(api.sessionSolo).mockImplementation(async () => session({ mode: 'auto', state: 'verifying', round: 2, undo_until_s: 29 }))
    vi.mocked(api.uploadRecording).mockImplementationOnce(async (_s, member) => { await new Promise<void>((r) => (release = r)); return ok(member) })
    await recordMember(w, 'a')
    vi.mocked(api.undoApply).mockResolvedValue(session({ mode: 'auto', state: 'restoring', round: 2, undo_until_s: null }))
    await w.get('[data-undo]').trigger('click'); await flushPromises()
    release(); await flushPromises()
    expect(w.find('[data-done]').exists()).toBe(true)
    expect(w.get('[data-step-status]').text()).not.toContain('Kitchen')
    expect(w.get('[data-step-status]').text()).toContain('back to where they were')
  })

  it('marks a member that left the group mid-session and goes on without it', async () => {
    vi.mocked(api.uploadRecording).mockImplementation(async (_s, member) => {
      if (member === 'b') throw new ApiError({ status: 409, code: 'member_gone' })
      return ok(member)
    })
    vi.mocked(api.getSession).mockResolvedValue(session())
    const w = mountWizard()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    await w.get('[data-next]').trigger('click'); await flushPromises()
    await recordMember(w, 'a'); await recordMember(w, 'b')
    expect(w.get('[data-member-step="b"]').text()).toContain('left the group')
    expect(w.get('[data-to-proposal]').attributes('disabled')).toBeUndefined()
  })

  it('says why the proposal could not be computed and retries', async () => {
    vi.mocked(api.computeProposal).mockRejectedValueOnce(new ApiError({ status: 409, code: 'no_measurement' }))
    const w = mountWizard()
    await toProposal(w)
    expect(w.get('[data-proposal-error]').text()).toContain('no_measurement')
    await w.get('[data-retry-proposal]').trigger('click'); await flushPromises()
    expect(w.find('[data-row="b"]').exists()).toBe(true)
  })

  it('a session that finished under the check round shows done, never "ended"', async () => {
    vi.mocked(api.applyProposal).mockResolvedValue(session({ state: 'verifying', round: 2 }))
    const w = mountWizard()
    await toProposal(w)
    await w.get('[data-check-again]').setValue(true)
    await w.get('[data-apply]').trigger('click'); await flushPromises()
    useSyncStore().applyEvent(session({ state: 'done', round: 2 }))
    await flushPromises()
    expect(w.find('[data-done]').exists()).toBe(true)
    expect(w.text()).not.toContain('The measurement session ended')
    expect(rec.closed).toBe(2)
  })

  it('goes to the done step when the group starts restoring during the check round', async () => {
    vi.mocked(api.applyProposal).mockResolvedValue(session({ state: 'verifying', round: 2 }))
    const w = mountWizard()
    await toProposal(w)
    await w.get('[data-check-again]').setValue(true)
    await w.get('[data-apply]').trigger('click'); await flushPromises()
    useSyncStore().applyEvent(session({ state: 'restoring', round: 2, restore_error: 'ma_unavailable' }))
    await flushPromises()
    expect(w.get('[data-done]').text()).toContain('Restoring the group')
    expect(w.get('[data-listen]').attributes('disabled')).toBeDefined()
    expect(rec.closed).toBe(2)
  })

  it('plays a quick listen on the whole group for 10 s once the group is restored', async () => {
    vi.mocked(api.applyProposal).mockResolvedValue(session({ state: 'done' }))
    const w = mountWizard()
    await toProposal(w)
    await w.get('[data-apply]').trigger('click'); await flushPromises()
    vi.mocked(api.createSession).mockResolvedValue(session({ id: 'q1', mode: 'manual', signal_running: false }))
    vi.mocked(api.sessionSignal).mockResolvedValue(session({ id: 'q1', mode: 'manual' }))
    await w.get('[data-listen]').trigger('click'); await flushPromises()
    expect(api.createSession).toHaveBeenLastCalledWith({ group_id: 'g', mode: 'manual' })
    expect(api.sessionSignal).toHaveBeenCalledWith('q1', 'start')
    // The quick listen never becomes the page's session, even when its events arrive.
    useSyncStore().applyEvent(session({ id: 'q1', mode: 'manual', state: 'measuring' }))
    expect(useSyncStore().session?.id).toBe('s1')
    expect(useSyncStore().session?.state).toBe('done')
    expect(w.get('[data-listen]').attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(10_000); await flushPromises()
    expect(api.sessionSignal).toHaveBeenCalledWith('q1', 'stop')
    expect(api.endSession).toHaveBeenCalledWith('q1')
    useSyncStore().applyEvent(session({ id: 'q1', mode: 'manual', state: 'cancelled' }))
    expect(useSyncStore().session?.state).toBe('done')
  })

  it('cancel on the proposal ends the session (the microphone is already closed)', async () => {
    const w = mountWizard()
    await toProposal(w)
    await w.get('[data-cancel]').trigger('click'); await flushPromises()
    expect(api.endSession).toHaveBeenCalledWith('s1')
    expect(rec.closed).toBe(1)
    expect(w.emitted('done')).toHaveLength(1)
  })
})
