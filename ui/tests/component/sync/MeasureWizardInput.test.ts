import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import { useSyncStore } from '@/stores/sync'
import { ApiError } from '@/api/client'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'
import de from '@/i18n/locales/de.json'
import es from '@/i18n/locales/es.json'
import fr from '@/i18n/locales/fr.json'
import it_ from '@/i18n/locales/it.json'
import ja from '@/i18n/locales/ja.json'
import ko from '@/i18n/locales/ko.json'
import tr from '@/i18n/locales/tr.json'
import zhHans from '@/i18n/locales/zh-Hans.json'

vi.mock('@/api/sync', () => ({
  createSession: vi.fn(), getSession: vi.fn(), sessionSignal: vi.fn(), sessionSolo: vi.fn(), sessionSkip: vi.fn(),
  sessionVolume: vi.fn(), startSequence: vi.fn(), captureInput: vi.fn(), getListeningDevices: vi.fn(),
  computeProposal: vi.fn(), applyProposal: vi.fn(), endSession: vi.fn(), finishSession: vi.fn(), resetSession: vi.fn(),
  captureProfile: vi.fn(), endSessionOnPageHide: vi.fn(), getSyncGroup: vi.fn(), listProfiles: vi.fn(), uploadRecording: vi.fn(),
  captureLink: vi.fn(),
}))
import * as api from '@/api/sync'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [
  { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'b', name: 'Hall', origin: 'sendspin', adjustable: true, delay_ms: 40 },
]
const DEVICES = {
  devices: [
    { id: 'line', name: 'Living room line-in', kind: 'local', available: true },
    { id: 'mic', name: 'Headset microphone', kind: 'headset_microphone', available: true },
  ],
}
const sequence = (over: Record<string, unknown> = {}) => ({
  members: ['a', 'b'], phase: 'starting', index: -1, member: null, error: null, error_member: null,
  solo_s: 7, gap_s: 5, lead_s: 3, uploaded: false, ...over,
})
const session = (over: Record<string, unknown> = {}) =>
  ({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, departed: [], measurements: {},
     clicker_playing: true, clicker_starts: 1, group_state: 'playing', volumes: { a: 40, b: 40 }, muted: [], sequence: null, ...over }) as never
const ok = (member: string): never => ({ member, status: 'ok', confidence: 0.9, clicks: 3, phase_ms: 1, spread_ms: 0.3, reason: '' }) as never
const proposal = {
  reference: 'a', predicted_spread_ms: 1, unverified: [], warnings: [],
  rows: [{ member: 'a', before_ms: 0, after_ms: 0, delta_ms: 0, clamped: false }, { member: 'b', before_ms: 40, after_ms: 52, delta_ms: 12, clamped: false }],
}
const tuned = (over: Record<string, unknown> = {}) =>
  session({ state: 'tuning', proposal, history: [{ spread_before_ms: 12 }], sequence: sequence({ phase: 'done', uploaded: true }), ...over })

class FakeRecorder {
  static supported = () => true
  opened = 0
  async open() { this.opened++ }
  level() { return 0.2 }
  async record(d: number) { return { pcm: new Int16Array(16000 * d), sampleRate: 16000, startTimeS: 20 } }
  close() {}
}

describe('MeasureWizard: a bridge input as the listening device', () => {
  let rec: FakeRecorder
  beforeEach(() => {
    setActivePinia(createPinia()); vi.useFakeTimers(); vi.clearAllMocks(); rec = new FakeRecorder()
    useSyncStore().group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.getListeningDevices).mockResolvedValue(DEVICES as never)
    vi.mocked(api.createSession).mockResolvedValue(session())
    vi.mocked(api.endSession).mockResolvedValue(undefined as never)
    vi.mocked(api.sessionSkip).mockResolvedValue(session())
    vi.mocked(api.sessionVolume).mockResolvedValue(session())
  })
  afterEach(() => { vi.useRealTimers() })

  const mountWizard = () =>
    mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', recorderFactory: () => rec as never } })

  /** prepare → clicker → volume → members (the fixture's clicker already plays). */
  async function toMembers(w: ReturnType<typeof mount>) {
    for (let i = 0; i < 3; i++) { await w.get('[data-next]').trigger('click'); await flushPromises() }
  }

  /** Choose one of the bridge's inputs on the prepare step. */
  async function chooseInput(w: ReturnType<typeof mount>, id = 'line') {
    await flushPromises()
    await w.get(`[data-listening-input="${id}"]`).setValue(true)
  }

  it('offers each of the bridge’s inputs next to this device’s microphone and the phone, device still the default', async () => {
    const w = mountWizard()
    await flushPromises()
    expect(api.getListeningDevices).toHaveBeenCalledTimes(1)
    expect((w.get('[data-capture="device"]').element as HTMLInputElement).checked).toBe(true)
    expect(w.find('[data-capture="phone"]').exists()).toBe(true)
    const line = w.get('[data-listening-input="line"]')
    expect(line.exists()).toBe(true)
    expect(w.text()).toContain('Living room line-in')
    expect(w.text()).toContain('Headset microphone')
    expect((line.element as HTMLInputElement).checked).toBe(false)
  })

  it('does not offer the bridge list when the bridge reports no input', async () => {
    vi.mocked(api.getListeningDevices).mockResolvedValue({ devices: [] } as never)
    const w = mountWizard()
    await flushPromises()
    expect(api.getListeningDevices).toHaveBeenCalledTimes(1)
    expect(w.find('[data-listening-input]').exists()).toBe(false)
    expect(w.get('[data-capture="phone"]').exists()).toBe(true)
    expect((w.get('[data-capture="device"]').element as HTMLInputElement).checked).toBe(true)
  })

  it('lists an input the bridge cannot see, disabled and said so', async () => {
    vi.mocked(api.getListeningDevices).mockResolvedValue({
      devices: [{ id: 'gone', name: 'Patio microphone', kind: 'local', available: false }],
    } as never)
    const w = mountWizard()
    await flushPromises()
    const gone = w.get('[data-listening-input="gone"]')
    expect((gone.element as HTMLInputElement).disabled).toBe(true)
    expect(w.text()).toContain('Patio microphone')
    expect(w.text()).toContain('Unavailable')
  })
  it('records the sequence from the bridge input, measuring every member from the one recording', async () => {
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockResolvedValue({ results: [ok('a'), ok('b')] } as never)
    vi.mocked(api.getSession).mockResolvedValue(
      session({ measurements: { a: ok('a'), b: ok('b') }, sequence: sequence({ phase: 'done', uploaded: true }) }),
    )
    vi.mocked(api.computeProposal).mockResolvedValue(proposal as never)
    vi.mocked(api.applyProposal).mockResolvedValue(tuned())
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    expect(rec.opened).toBe(0)                                   // nothing is recorded in this browser
    expect(w.find('[data-listening-capture]').exists()).toBe(true)
    expect(w.find('[data-member-step]').exists()).toBe(false)
    expect(w.find('[data-phone]').exists()).toBe(false)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    expect(api.startSequence).toHaveBeenCalledWith('s1', ['a', 'b'])
    // 3 s lead-in + 2 × 7 s alone + 5 s between: the sequence's own length
    expect(api.captureInput).toHaveBeenCalledWith('s1', 'line', 22)
    expect(w.find('[data-tune]').exists()).toBe(true)            // the same result screen as the phone
  })

  it('shows the phone branch’s progress while the bridge records', async () => {
    let answer!: (v: unknown) => void
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockImplementation(() => new Promise((resolve) => (answer = resolve)) as never)
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    expect(w.get('[data-sequence-status]').text()).toBe('Starting: every speaker is quiet for a moment…')
    useSyncStore().applyEvent(session({ sequence: sequence({ phase: 'solo', index: 0, member: 'a' }) }))
    await flushPromises()
    expect(w.get('[data-sequence-status]').text()).toBe('Now: Kitchen alone (1/2)')
    useSyncStore().applyEvent(session({ sequence: sequence({ phase: 'gap', index: 0, member: null }) }))
    await flushPromises()
    expect(w.get('[data-sequence-status]').text()).toBe('Pause: move to the next speaker…')
    expect(w.get('[data-record-sequence]').attributes('disabled')).toBeDefined()
    answer({ results: [ok('a'), ok('b')] })
    await flushPromises()
  })
  it('says the listening device disappeared, names it and offers the phone path', async () => {
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockRejectedValue(new ApiError({ status: 409, code: 'input_unavailable' }))
    vi.mocked(api.captureLink).mockResolvedValue({ url: 'http://b/sync/capture/t', http_url: 'http://b/sync/capture/t', secure: false, token: 't', expires_in_s: 1800 } as never)
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    expect(w.get('[data-input-failure]').text()).toContain('Living room line-in')
    expect(w.get('[data-input-failure]').text()).toContain('no longer available')
    expect(w.find('[data-tune]').exists()).toBe(false)                 // no empty result screen
    expect(w.find('[data-record-sequence] [role="status"]').exists()).toBe(false)
    await w.get('[data-use-phone]').trigger('click')
    await flushPromises()
    expect(w.find('[data-phone]').exists()).toBe(true)
    expect(w.find('[data-listening-capture]').exists()).toBe(false)
  })

  it('says the source feed holds the input, names it and offers the phone path', async () => {
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockRejectedValue(new ApiError({ status: 409, code: 'input_busy' }))
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    expect(w.get('[data-input-failure]').text()).toContain('Living room line-in')
    expect(w.get('[data-input-failure]').text()).toContain('busy with the source feed')
    expect(w.find('[data-use-phone]').exists()).toBe(true)
    expect(w.find('[data-tune]').exists()).toBe(false)
  })

  it('keeps any other failure on the step, with its code and no result screen', async () => {
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockRejectedValue(new ApiError({ status: 409, code: 'session_state' }))
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    expect(w.get('[data-input-error]').text()).toContain('session_state')
    expect(w.find('[data-input-failure]').exists()).toBe(false)
    expect(w.find('[data-use-phone]').exists()).toBe(false)
    expect(w.find('[data-listening-capture]').exists()).toBe(true)
    expect(w.find('[data-tune]').exists()).toBe(false)
  })
  it('cancels cleanly while the bridge records: back to prepare, no spinner, the session ended', async () => {
    let answer!: (v: unknown) => void
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockImplementation(() => new Promise((resolve) => (answer = resolve)) as never)
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    expect(w.find('[data-record-sequence] [role="status"]').exists()).toBe(true)   // it is recording
    await w.get('[data-listening-capture] [data-cancel]').trigger('click')
    await flushPromises()
    expect(w.find('[data-listening-capture]').exists()).toBe(false)
    expect(w.find('[data-record-sequence] [role="status"]').exists()).toBe(false)
    expect(w.find('[data-phone]').exists()).toBe(false)
    expect(w.get('[data-step-status]').text()).toContain('Prepare (1/4)')
    expect(api.endSession).toHaveBeenCalledWith('s1')
    // the recording's answer arriving late must not revive anything
    answer({ results: [ok('a'), ok('b')] })
    await flushPromises()
    expect(w.find('[data-listening-capture]').exists()).toBe(false)
    expect(w.find('[data-tune]').exists()).toBe(false)
  })

  it('leaves nothing spinning when the wizard is unmounted mid-recording', async () => {
    let answer!: (v: unknown) => void
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockImplementation(() => new Promise((resolve) => (answer = resolve)) as never)
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    w.unmount()
    expect(vi.getTimerCount()).toBe(0)
    expect(api.endSession).toHaveBeenCalledWith('s1')
    answer({ results: [ok('a'), ok('b')] })
    await flushPromises()
  })
  it('marks the result as uncalibrated when a bridge input recorded, on the result and done screens', async () => {
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: sequence() }))
    vi.mocked(api.captureInput).mockResolvedValue({ results: [ok('a'), ok('b')] } as never)
    vi.mocked(api.getSession).mockResolvedValue(
      session({ measurements: { a: ok('a'), b: ok('b') }, sequence: sequence({ phase: 'done', uploaded: true }) }),
    )
    vi.mocked(api.computeProposal).mockResolvedValue(proposal as never)
    vi.mocked(api.applyProposal).mockResolvedValue(tuned())
    vi.mocked(api.finishSession).mockResolvedValue(tuned({ state: 'restoring' }))
    const w = mountWizard()
    await chooseInput(w)
    await toMembers(w)
    await w.get('[data-record-sequence]').trigger('click')
    await flushPromises()
    const note = 'Note: input latency is not calibrated — a constant delay shifts every speaker equally, so the relative result is unaffected.'
    expect(w.get('[data-tune] [data-input-latency-note]').text()).toBe(note)
    await w.get('[data-finish]').trigger('click')
    await flushPromises()
    expect(w.get('[data-done] [data-input-latency-note]').text()).toBe(note)
  })
const LOCALES: Record<string, typeof en> = { en, ru, de, es, fr, it: it_, ja, ko, tr, 'zh-Hans': zhHans }

/** Every key of a nested message object, dotted. */
function flatKeys(o: unknown, prefix = ''): string[] {
  return Object.entries(o as Record<string, unknown>).flatMap(([k, v]) =>
    v && typeof v === 'object' ? flatKeys(v, `${prefix}${k}.`) : [`${prefix}${k}`],
  )
}
function message(o: unknown, key: string): string {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)[part], o) as string
}
function child(o: unknown, prefix: string): unknown {
  return prefix.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], o)
}
const placeholders = (text: string) => [...new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))].sort()

describe('listening-device texts', () => {
  it('are in every locale, with every placeholder kept', () => {
    const groups = [
      ['sync.input', en.sync.input],
      ['sync.wizard.capture', en.sync.wizard.capture],
    ] as const
    for (const [id, messages] of Object.entries(LOCALES)) {
      for (const [prefix, english] of groups) {
        const local = child(messages, prefix)
        expect(local, `${id}:${prefix}`).toBeDefined()
        expect(flatKeys(local).sort(), `${id}:${prefix}`).toEqual(flatKeys(english).sort())
        for (const key of flatKeys(english)) {
          expect(placeholders(message(local, key)), `${id}:${prefix}.${key}`).toEqual(placeholders(message(english, key)))
        }
      }
    }
    // the honest note is the agreed wording, not a limitation
    expect(message(en, 'sync.input.latencyNote')).toContain('input latency is not calibrated')
    expect(message(en, 'sync.input.latencyNote')).toContain('relative result is unaffected')
  })
})
  it('takes the bridge’s list whether it answers {devices} or a bare array', async () => {
    vi.mocked(api.getListeningDevices).mockResolvedValue([{ id: 'line', name: 'Living room line-in', kind: 'local', available: true }] as never)
    const w = mountWizard()
    await flushPromises()
    expect(w.find('[data-listening-input="line"]').exists()).toBe(true)
  })
})
