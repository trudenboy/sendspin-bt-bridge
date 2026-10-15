import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import SyncView from '@/views/SyncView.vue'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({
  getSyncGroup: vi.fn(), getSession: vi.fn(), listRestores: vi.fn(), retryRestore: vi.fn(), listProfiles: vi.fn(),
  createSession: vi.fn(), endSession: vi.fn(), captureProfile: vi.fn(), sessionDelay: vi.fn(), sessionSignal: vi.fn(),
  sessionSolo: vi.fn(), sessionAb: vi.fn(), writeGroupDelays: vi.fn(),
}))
import * as api from '@/api/sync'
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
import { confirmDialog } from '@/composables/useConfirm'

const members = [
  { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 120 },
  { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null },
]
const group = { id: 'g', name: 'Downstairs', session_id: null, restore_pending: null, error: null, members }
const session = { id: 's1', group_id: 'g', mode: 'manual', state: 'measuring', members, departed: [], signal_running: false, soloed: null }

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/groups', name: 'groups', component: { template: '<div />' } },
      { path: '/sync/:groupId', name: 'sync', component: SyncView },
    ],
  })
  await router.push('/sync/g')
  const w = mount(
    { template: '<RouterView />' },
    { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router] }, attachTo: document.body },
  )
  await flushPromises()
  return { w, router }
}

async function openManual(w: Awaited<ReturnType<typeof mountView>>['w']) {
  await w.get('[role="tab"][data-tab-id="manual"]').trigger('click')
  await flushPromises()
}

describe('SyncView manual tuning', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(api.listRestores).mockResolvedValue([])
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.getSyncGroup).mockResolvedValue(structuredClone(group) as never)
    vi.mocked(api.createSession).mockResolvedValue(structuredClone(session) as never)
    vi.mocked(api.endSession).mockResolvedValue(undefined)
    vi.mocked(api.sessionDelay).mockResolvedValue({} as never)
    vi.mocked(api.writeGroupDelays).mockResolvedValue({} as never)
    vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p9', name: 'Evening', group_id: 'g', kind: 'device', delays: {}, notes: '', created_at: '' } as never)
    vi.mocked(confirmDialog).mockResolvedValue(true)
  })
  afterEach(() => { document.body.innerHTML = '' })

  it('starts a manual session when the manual tab opens', async () => {
    const { w } = await mountView()
    await openManual(w)
    expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'manual', point: 'device' })
    expect(w.findAll('[data-delay-row]')).toHaveLength(1)
    w.unmount()
  })

  it('does not start a second session when this group already has a manual one', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...structuredClone(group), session_id: 's1' } as never)
    vi.mocked(api.getSession).mockResolvedValue(structuredClone(session) as never)
    const { w } = await mountView()
    await openManual(w)
    expect(api.createSession).not.toHaveBeenCalled()
    expect(w.findAll('[data-delay-row]')).toHaveLength(1)
    w.unmount()
  })

  it('does not take over a measurement in progress', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...structuredClone(group), session_id: 's2' } as never)
    vi.mocked(api.getSession).mockResolvedValue({ ...structuredClone(session), id: 's2', mode: 'semi' } as never)
    const { w } = await mountView()
    await openManual(w)
    expect(api.createSession).not.toHaveBeenCalled()
    expect(w.find('[data-delay-row]').exists()).toBe(false)
    expect(w.get('[data-manual-busy]').text()).toContain('Another tuning')
    w.unmount()
  })

  it('ends the session without asking when nothing changed', async () => {
    const { w } = await mountView()
    await openManual(w)
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click')
    await flushPromises()
    expect(confirmDialog).not.toHaveBeenCalled()
    expect(api.endSession).toHaveBeenCalledWith('s1')
    w.unmount()
  })

  it('offers to save the changed delays as a profile, then ends the session', async () => {
    const { w } = await mountView()
    await openManual(w)
    await w.get('[data-delay-row] [aria-label="+10 ms"]').trigger('click')
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click')
    await flushPromises()
    expect(confirmDialog).toHaveBeenCalledTimes(1)
    expect(api.endSession).not.toHaveBeenCalled()
    const name = document.body.querySelector<HTMLInputElement>('[data-manual-profile-name]')!
    name.value = 'Evening'
    name.dispatchEvent(new Event('input'))
    ;(document.body.querySelector('[data-manual-profile-save]') as HTMLElement).click()
    await flushPromises()
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Evening', kind: 'device' })
    expect(api.endSession).toHaveBeenCalledWith('s1')
    w.unmount()
  })

  it('ends the session when the save offer is declined', async () => {
    vi.mocked(confirmDialog).mockResolvedValue(false)
    const { w } = await mountView()
    await openManual(w)
    await w.get('[data-delay-row] [aria-label="+10 ms"]').trigger('click')
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click')
    await flushPromises()
    expect(api.captureProfile).not.toHaveBeenCalled()
    expect(api.endSession).toHaveBeenCalledWith('s1')
    w.unmount()
  })

  it('offers to save before the page is left, then ends the session', async () => {
    vi.mocked(confirmDialog).mockResolvedValue(false)
    const { w, router } = await mountView()
    await openManual(w)
    await w.get('[data-delay-row] [aria-label="+10 ms"]').trigger('click')
    await router.push('/groups')
    await flushPromises()
    expect(confirmDialog).toHaveBeenCalledTimes(1)
    expect(api.endSession).toHaveBeenCalledWith('s1')
    expect(router.currentRoute.value.name).toBe('groups')
    w.unmount()
  })
})
