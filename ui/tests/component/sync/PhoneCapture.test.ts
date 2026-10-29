import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import PhoneCapture from '@/components/sync/PhoneCapture.vue'
import { useSyncStore } from '@/stores/sync'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({
  createSession: vi.fn(), getSession: vi.fn(), sessionSignal: vi.fn(), sessionSolo: vi.fn(), sessionSkip: vi.fn(),
  captureLink: vi.fn(), startSequence: vi.fn(), endSession: vi.fn(), endSessionOnPageHide: vi.fn(), uploadRecording: vi.fn(),
  computeProposal: vi.fn(), applyProposal: vi.fn(), sessionVolume: vi.fn(),
}))
import * as api from '@/api/sync'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [
  { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'b', name: 'Hall @ 85b1ecde-bridge', origin: 'sendspin', adjustable: true, delay_ms: 0 },
]
const session = (over: Record<string, unknown> = {}) =>
  ({ id: 's1', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, departed: [], measurements: {},
     clicker_playing: true, clicker_starts: 1, group_state: 'playing', volumes: { a: 40, b: 40 }, muted: [], sequence: null, ...over }) as never
const LINK = { url: 'http://192.168.1.5:8080/sync/capture/tok?lang=en', token: 'tok', expires_in_s: 1800 }

describe('measuring with a phone', () => {
  beforeEach(() => {
    setActivePinia(createPinia()); vi.clearAllMocks()
    useSyncStore().group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.captureLink).mockResolvedValue(LINK)
    vi.mocked(api.createSession).mockResolvedValue(session())
    vi.mocked(api.sessionSkip).mockImplementation(async () => session({ measurements: { b: { member: 'b', status: 'skipped' } } }))
  })

  it('shows the link as a QR code, plays the sequence for the speakers not skipped, and follows it', async () => {
    useSyncStore().session = session()
    vi.mocked(api.startSequence).mockResolvedValue(session({ sequence: { members: ['a'], phase: 'starting', index: -1, member: null, error: null, error_member: null, solo_s: 7, gap_s: 5, lead_s: 3, uploaded: false } }))
    const w = mount(PhoneCapture, { global: { plugins: [i18n] }, props: { session: session(), members: members as never } })
    await flushPromises()
    expect(api.captureLink).toHaveBeenCalledWith('s1', 'en')
    expect(w.get('[data-qr] path').attributes('d')).toMatch(/^M0 0h1v1h-1z/)
    expect(w.get('[data-link]').text()).toBe(LINK.url)
    expect(w.text()).toContain('It works for 30 minutes and stops when the measurement ends.')
    expect(w.text()).toContain('Hall')
    expect(w.text()).not.toContain('85b1ecde')
    await w.get('[data-phone-skip="b"]').trigger('click'); await flushPromises()
    expect(api.sessionSkip).toHaveBeenCalledWith('s1', 'b')
    expect(w.get('[data-play-sequence]').text()).toContain('Play the sequence (1)')
    await w.get('[data-play-sequence]').trigger('click'); await flushPromises()
    expect(api.startSequence).toHaveBeenCalledWith('s1', ['a'])
    await w.setProps({ session: session({ sequence: { members: ['a'], phase: 'solo', index: 0, member: 'a', error: null, error_member: null, solo_s: 7, gap_s: 5, lead_s: 3, uploaded: false } }) })
    expect(w.get('[data-sequence-status]').text()).toBe('Now: Kitchen alone (1/1)')
    expect(w.get('[data-play-sequence]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-to-tune]').attributes('disabled')).toBeDefined()
    await w.setProps({ session: session({
      measurements: { a: { member: 'a', status: 'ok' }, b: { member: 'b', status: 'skipped' } },
      sequence: { members: ['a'], phase: 'done', index: 0, member: null, error: null, error_member: null, solo_s: 7, gap_s: 5, lead_s: 3, uploaded: true } }) })
    expect(w.get('[data-sequence-status]').text()).toBe('The phone’s recording is measured.')
    expect(w.get('[data-phone-member="a"]').text()).toContain('Heard')
    expect(w.get('[data-to-tune]').attributes('disabled')).toBeUndefined()
  })

  it('lets the measured speakers be applied when one was not heard, and says it keeps its delay', async () => {
    const done = session({
      measurements: { a: { member: 'a', status: 'ok' }, b: { member: 'b', status: 'not_heard' } },
      sequence: { members: ['a', 'b'], phase: 'done', index: 1, member: null, error: null, error_member: null, solo_s: 7, gap_s: 5, lead_s: 3, uploaded: true } })
    const w = mount(PhoneCapture, { global: { plugins: [i18n] }, props: { session: done, members: members as never } })
    await flushPromises()
    expect(w.get('[data-to-tune]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-keeps-delay]').text()).toContain('Hall')
  })

  it('keeps the next step closed until at least one speaker was heard', async () => {
    const none = session({
      measurements: { a: { member: 'a', status: 'not_heard' }, b: { member: 'b', status: 'unstable' } },
      sequence: { members: ['a', 'b'], phase: 'done', index: 1, member: null, error: null, error_member: null, solo_s: 7, gap_s: 5, lead_s: 3, uploaded: true } })
    const w = mount(PhoneCapture, { global: { plugins: [i18n] }, props: { session: none, members: members as never } })
    await flushPromises()
    expect(w.get('[data-to-tune]').attributes('disabled')).toBeDefined()
  })

  it('needs the clicks to play the sequence and says so', async () => {
    const w = mount(PhoneCapture, { global: { plugins: [i18n] }, props: { session: session({ clicker_playing: false }), members: members as never } })
    await flushPromises()
    expect(w.get('[data-play-sequence]').attributes('disabled')).toBeDefined()
    expect(w.get('#sync-phone-why').text()).toContain('Clicks are not playing.')
    await w.get('[data-restart-clicker]').trigger('click')
    expect(w.emitted('restart')).toHaveLength(1)
  })

  it('in the wizard, a phone needs no microphone here and replaces the speaker-by-speaker step', async () => {
    const open = vi.fn()
    const w = mount(MeasureWizard, { global: { plugins: [i18n] }, props: { groupId: 'g', recorderFactory: () => ({ open }) as never } })
    await w.get('[data-capture="phone"]').setValue(true)
    expect(w.get('[data-next]').text()).toBe('Start')
    for (let i = 0; i < 3; i++) { await w.get('[data-next]').trigger('click'); await flushPromises() }
    expect(open).not.toHaveBeenCalled()
    expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'semi', point: 'device' })
    expect(w.find('[data-phone]').exists()).toBe(true)
    expect(w.find('[data-member-step]').exists()).toBe(false)
  })
})

describe('phone sequence: Cancel', () => {
  it('stays active while the sequence runs, Back does not', async () => {
    useSyncStore().group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.captureLink).mockResolvedValue(LINK)
    const running = session({ sequence: { members: ['a'], phase: 'solo', index: 0, member: 'a', error: null, error_member: null, solo_s: 7, gap_s: 5, lead_s: 3, uploaded: false } })
    useSyncStore().session = running
    const w = mount(PhoneCapture, { global: { plugins: [i18n] }, props: { session: running, members: members as never } })
    await flushPromises()
    expect(w.get('[data-cancel]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-back]').attributes('disabled')).toBeDefined()
    await w.get('[data-cancel]').trigger('click')
    expect(w.emitted('cancel')).toHaveLength(1)
  })
})
