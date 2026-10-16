import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import SyncView from '@/views/SyncView.vue'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({
  getSyncGroup: vi.fn(), getSession: vi.fn(), listRestores: vi.fn(), retryRestore: vi.fn(), listProfiles: vi.fn(),
  endSession: vi.fn(), createSession: vi.fn(), endSessionOnPageHide: vi.fn(),
}))
import * as api from '@/api/sync'
import { useSyncStore } from '@/stores/sync'
import { settleConfirm } from '@/composables/useConfirm'

const members = [{ ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 120 }]
const group = { id: 'g', name: 'Downstairs', session_id: null, restore_pending: null, error: null, members }
const session = (over: Record<string, unknown> = {}) =>
  ({ id: 's9', group_id: 'g', mode: 'semi', point: 'device', state: 'measuring', members, departed: [], measurements: {}, history: [],
     signal_running: true, error: null, restore_error: null, proposal: null, reference: null, round: 1, soloed: null, undo_until_s: null, ...over }) as never

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/groups', name: 'groups', component: { template: '<div />' } },
      { path: '/sync/:groupId', name: 'sync', component: SyncView },
    ],
  })
  await router.push('/sync/g')
  const w = mount(SyncView, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router] } })
  await flushPromises()
  return w
}

describe('SyncView: sessions of other clients and restores', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(api.listRestores).mockResolvedValue([])
    vi.mocked(api.listProfiles).mockResolvedValue([])
  })

  it('is read-only while another device tunes the group', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    vi.mocked(api.getSession).mockResolvedValue(session())
    const w = await mountView()
    expect(w.get('[data-read-only]').text()).toContain('Tuning in progress on another device')
    for (const b of w.findAll('[data-action]')) expect(b.attributes('disabled')).toBeDefined()
  })

  it('is read-only when the other session cannot be read', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    vi.mocked(api.getSession).mockRejectedValue(new Error('down'))
    const w = await mountView()
    expect(w.find('[data-read-only]').exists()).toBe(true)
  })

  it('lets the operator end the other session after confirming (nobody is locked out)', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    vi.mocked(api.getSession).mockRejectedValue(new Error('down'))
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    const w = await mountView()
    await w.get('[data-end-foreign]').trigger('click'); await flushPromises()
    expect(api.endSession).not.toHaveBeenCalled()             // asks first
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    settleConfirm(true); await flushPromises()
    expect(api.endSession).toHaveBeenCalledWith('s9')
    expect(w.find('[data-read-only]').exists()).toBe(false)
  })

  it('does not end the other session when the operator declines', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    vi.mocked(api.getSession).mockRejectedValue(new Error('down'))
    const w = await mountView()
    await w.get('[data-end-foreign]').trigger('click'); await flushPromises()
    settleConfirm(false); await flushPromises()
    expect(api.endSession).not.toHaveBeenCalled()
  })

  it('disables applying a profile while read-only', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    vi.mocked(api.getSession).mockResolvedValue(session())
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', group_id: 'g', name: 'Evening', kind: 'device', delays: { a: 10 }, notes: '', created_at: '' }] as never)
    const w = await mountView()
    expect(w.get('[data-apply="p1"]').attributes('disabled')).toBeDefined()
  })

  it('is not read-only after this page ends its own session, even if the group was read during it', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    vi.mocked(api.createSession).mockResolvedValue(session())
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    const w = await mountView()
    const store = useSyncStore()
    await store.start('g', 'semi')
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    await store.fetchGroup('g')                                  // e.g. a resync while it ran
    await store.end()
    await flushPromises()
    expect(w.find('[data-read-only]').exists()).toBe(false)
  })

  it('keeps the finished outcome in the header while a quick listen runs', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    vi.mocked(api.createSession).mockResolvedValue(session())
    const w = await mountView()
    const store = useSyncStore()
    await store.start('g', 'semi')
    store.applyEvent(session({ state: 'done' }))
    const release = store.holdSession()
    store.applyEvent(session({ id: 'q1', mode: 'manual', state: 'measuring' }))   // before its id is known
    await flushPromises()
    expect(w.get('h1 + p').text()).toBe('Done')
    store.ignoreSession('q1')
    release()
    store.applyEvent(session({ id: 'q1', mode: 'manual', state: 'cancelled' }))
    await flushPromises()
    expect(w.get('h1 + p').text()).toBe('Done')
  })

  it('is not read-only for a session this page started', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    vi.mocked(api.createSession).mockResolvedValue(session())
    const w = await mountView()
    await useSyncStore().start('g', 'semi')
    await flushPromises()
    expect(w.find('[data-read-only]').exists()).toBe(false)
    for (const b of w.findAll('[data-action]')) expect(b.attributes('disabled')).toBeUndefined()
  })

  it('shows "Could not restore the group" with Retry while the session keeps failing to restore', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    vi.mocked(api.createSession).mockResolvedValue(session())
    const w = await mountView()
    await useSyncStore().start('g', 'semi')
    useSyncStore().applyEvent(session({ state: 'restoring', restore_error: 'ma_unavailable' }))
    await flushPromises()
    expect(w.text()).toContain('Could not restore the group')
    expect(w.text()).toContain('ma_unavailable')
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    vi.mocked(api.getSession).mockResolvedValue(session({ state: 'done' }))
    await w.get('[data-retry-session]').trigger('click'); await flushPromises()
    expect(api.endSession).toHaveBeenCalledWith('s9')
    expect(api.getSession).toHaveBeenCalledWith('s9')
    expect(w.text()).not.toContain('Could not restore the group')
    // A retry never turns a finished session into a cancelled one.
    expect(useSyncStore().session?.state).toBe('done')
    expect(w.text()).not.toContain('Cancelled')
  })

  it('says when the music returns while the last test clip plays out, without going read-only', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, session_id: 's9' } as never)
    vi.mocked(api.getSession).mockResolvedValue(session({ state: 'restoring', music_returns_in_s: 75 }))
    const w = await mountView()
    expect(w.get('[data-clip-ending]').text()).toContain('Music returns in 75 s')
    expect(w.find('[data-read-only]').exists()).toBe(false)
  })
})

