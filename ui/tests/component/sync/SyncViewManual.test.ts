import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import SyncView from '@/views/SyncView.vue'
import en from '@/i18n/locales/en.json'
import { defineComponent, h, nextTick, ref } from 'vue'

vi.mock('@/api/sync', () => ({
  getSyncGroup: vi.fn(), getSession: vi.fn(), listRestores: vi.fn(), retryRestore: vi.fn(), listProfiles: vi.fn(),
  createSession: vi.fn(), endSession: vi.fn(), finishSession: vi.fn(), captureProfile: vi.fn(), sessionDelay: vi.fn(), sessionSignal: vi.fn(),
  sessionSolo: vi.fn(), sessionAb: vi.fn(), writeGroupDelays: vi.fn(), endSessionOnPageHide: vi.fn(),
}))
import * as api from '@/api/sync'
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
import { confirmDialog } from '@/composables/useConfirm'
import { useSyncStore } from '@/stores/sync'
import { ApiError } from '@/api/client'

const members = [
  { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 120 },
  { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null },
]
const group = { id: 'g', name: 'Downstairs', session_id: null, restore_pending: null, error: null, members }
const session = { id: 's1', group_id: 'g', mode: 'manual', state: 'tuning', members, departed: [], clicker_playing: false, clicker_starts: 0, group_state: 'idle', volumes: {}, muted: [], soloed: null }

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

async function openTab(w: { get: Awaited<ReturnType<typeof mountView>>['w']['get'] }) {
  await w.get('[role="tab"][data-tab-id="manual"]').trigger('click')
  await flushPromises()
}

/** Opens the tab and, when it offers one, presses the explicit start button (opening alone starts nothing). */
async function openManual(w: Awaited<ReturnType<typeof mountView>>['w']) {
  await openTab(w)
  const start = w.find('[data-manual-start]')
  if (start.exists()) {
    await start.trigger('click')
    await flushPromises()
  }
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
    vi.mocked(api.finishSession).mockResolvedValue({ ...structuredClone(session), state: 'restoring' } as never)
    vi.mocked(api.sessionDelay).mockResolvedValue({} as never)
    vi.mocked(api.writeGroupDelays).mockResolvedValue({} as never)
    vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p9', name: 'Evening', group_id: 'g', kind: 'device', delays: {}, notes: '', created_at: '' } as never)
    vi.mocked(confirmDialog).mockResolvedValue(true)
  })
  afterEach(() => { document.body.innerHTML = '' })

  it('never starts a session by just opening the manual tab; the start button does', async () => {
    const { w } = await mountView()
    await openTab(w)
    expect(api.createSession).not.toHaveBeenCalled()
    expect(w.find('[data-delay-row]').exists()).toBe(false)
    await w.get('[data-manual-start]').trigger('click'); await flushPromises()
    expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'manual', point: 'device' })
    expect(w.findAll('[data-delay-row]')).toHaveLength(1)
    w.unmount()
  })

  it('starts the session from the Group tab entry button (an explicit request)', async () => {
    const { w } = await mountView()
    await w.get('[data-action="manual"]').trigger('click'); await flushPromises()
    expect(api.createSession).toHaveBeenCalledWith({ group_id: 'g', mode: 'manual', point: 'device' })
    expect(w.findAll('[data-delay-row]')).toHaveLength(1)
    w.unmount()
  })

  it('never takes over a manual session another device started: it offers to join it (M10)', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...structuredClone(group), session_id: 's1' } as never)
    vi.mocked(api.getSession).mockResolvedValue(structuredClone(session) as never)
    const { w } = await mountView()
    await openManual(w)
    expect(api.createSession).not.toHaveBeenCalled()
    expect(w.find('[data-delay-row]').exists()).toBe(false)
    expect(w.get('[data-read-only]').text()).toContain('Manual tuning is open on another device')
    // Leaving the tab never ends someone else's tuning.
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click'); await flushPromises()
    expect(api.finishSession).not.toHaveBeenCalled()
    expect(api.endSession).not.toHaveBeenCalled()
    await openManual(w)
    await w.get('[data-join-manual]').trigger('click'); await flushPromises()
    expect(w.findAll('[data-delay-row]')).toHaveLength(1)
    // Joined: now this page finishes it when the tab is left.
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click'); await flushPromises()
    expect(api.finishSession).toHaveBeenCalledWith('s1')
    w.unmount()
  })

  it('does not take over a measurement in progress', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...structuredClone(group), session_id: 's2' } as never)
    vi.mocked(api.getSession).mockResolvedValue({ ...structuredClone(session), id: 's2', mode: 'semi', state: 'measuring' } as never)
    const { w } = await mountView()
    await openManual(w)
    expect(api.createSession).not.toHaveBeenCalled()
    expect(w.find('[data-delay-row]').exists()).toBe(false)
    expect(w.get('[data-read-only]').text()).toContain('Tuning in progress on another device')
    w.unmount()
  })

  it('finishes the session without asking when nothing changed, and says what happened', async () => {
    const { w } = await mountView()
    await openManual(w)
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click')
    await flushPromises()
    expect(confirmDialog).not.toHaveBeenCalled()
    expect(api.finishSession).toHaveBeenCalledWith('s1')
    expect(api.endSession).not.toHaveBeenCalled()
    expect(w.get('[data-left-notice]').text()).toContain('Manual tuning ended')
    w.unmount()
  })

  it('offers to save the changed delays as a profile, then ends the session', async () => {
    const { w } = await mountView()
    await openManual(w)
    await w.get('[data-delay-row] [aria-label="+10 ms (earlier)"]').trigger('click')
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click')
    await flushPromises()
    expect(confirmDialog).toHaveBeenCalledTimes(1)
    expect(api.finishSession).not.toHaveBeenCalled()
    const name = document.body.querySelector<HTMLInputElement>('[data-manual-profile-name]')!
    name.value = 'Evening'
    name.dispatchEvent(new Event('input'))
    ;(document.body.querySelector('[data-manual-profile-save]') as HTMLElement).click()
    await flushPromises()
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Evening', kind: 'device' })
    expect(api.finishSession).toHaveBeenCalledWith('s1')
    w.unmount()
  })

  it('ends the session when the save offer is declined', async () => {
    vi.mocked(confirmDialog).mockResolvedValue(false)
    const { w } = await mountView()
    await openManual(w)
    await w.get('[data-delay-row] [aria-label="+10 ms (earlier)"]').trigger('click')
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click')
    await flushPromises()
    expect(api.captureProfile).not.toHaveBeenCalled()
    expect(api.finishSession).toHaveBeenCalledWith('s1')
    w.unmount()
  })

  it('offers to save before the page is left, then ends the session', async () => {
    vi.mocked(confirmDialog).mockResolvedValue(false)
    const { w, router } = await mountView()
    await openManual(w)
    await w.get('[data-delay-row] [aria-label="+10 ms (earlier)"]').trigger('click')
    await router.push('/groups')
    await flushPromises()
    expect(confirmDialog).toHaveBeenCalledTimes(1)
    expect(api.finishSession).toHaveBeenCalledWith('s1')
    expect(router.currentRoute.value.name).toBe('groups')
    w.unmount()
  })

  it('says when the session ended under the open tab and starts again only on request', async () => {
    const { w } = await mountView()
    await openManual(w)
    const store = useSyncStore()
    store.applyEvent({ ...store.session!, state: 'cancelled' } as never)
    await flushPromises()
    expect(w.find('[data-delay-row]').exists()).toBe(false)
    const ended = w.get('[data-manual-ended]')
    expect(api.createSession).toHaveBeenCalledTimes(1)
    await ended.get('button').trigger('click')
    await flushPromises()
    expect(api.createSession).toHaveBeenCalledTimes(2)
    expect(w.find('[data-manual-ended]').exists()).toBe(false)
    expect(w.findAll('[data-delay-row]')).toHaveLength(1)
    w.unmount()
  })

  it('writes the last edit before asking and before ending', async () => {
    vi.mocked(confirmDialog).mockResolvedValue(false)
    const { w } = await mountView()
    await openManual(w)
    await w.get('[data-delay-row] [aria-label="+10 ms (earlier)"]').trigger('click')
    expect(api.sessionDelay).not.toHaveBeenCalled()
    await w.get('[role="tab"][data-tab-id="group"]').trigger('click')
    await flushPromises()
    expect(api.sessionDelay).toHaveBeenCalledWith('s1', 'a', 130)
    const wrote = vi.mocked(api.sessionDelay).mock.invocationCallOrder[0]!
    expect(wrote).toBeLessThan(vi.mocked(confirmDialog).mock.invocationCallOrder[0]!)
    expect(wrote).toBeLessThan(vi.mocked(api.finishSession).mock.invocationCallOrder[0]!)
    w.unmount()
  })

  it('ends the manual session when the page is hidden for good, and stops listening once gone', async () => {
    // The session outlives the view (its DELETE is still on the way): only the removed listener keeps quiet.
    vi.mocked(api.finishSession).mockReturnValue(new Promise(() => {}))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/groups', name: 'groups', component: { template: '<div />' } },
        { path: '/sync/:groupId', name: 'sync', component: SyncView },
      ],
    })
    await router.push('/sync/g')
    // The view goes away while the app and its route stay (unmounting the app would reset the route).
    const show = ref(true)
    const host = defineComponent({ setup: () => () => (show.value ? h(SyncView) : null) })
    const w = mount(host, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router] }, attachTo: document.body })
    await flushPromises()
    await openManual(w)
    window.dispatchEvent(new Event('pagehide'))
    expect(api.endSessionOnPageHide).toHaveBeenCalledWith('s1')
    show.value = false
    await nextTick()
    vi.mocked(api.endSessionOnPageHide).mockClear()
    window.dispatchEvent(new Event('pagehide'))
    expect(api.endSessionOnPageHide).not.toHaveBeenCalled()
    w.unmount()
  })

  it('says once, after a reload, that the manual tuning ended with it', async () => {
    sessionStorage.setItem('sync:active', JSON.stringify({ group: 'g', kind: 'manual' }))
    const { w } = await mountView()
    expect(w.get('[data-reload-notice]').text()).toContain('Manual tuning ended when the page was reloaded')
    expect(sessionStorage.getItem('sync:active')).toBeNull()
    w.unmount()
  })
})
