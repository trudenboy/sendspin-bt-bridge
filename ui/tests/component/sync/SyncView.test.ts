import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import SyncView from '@/views/SyncView.vue'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import en from '@/i18n/locales/en.json'

vi.mock('@/api/sync', () => ({
  getSyncGroup: vi.fn(), getSession: vi.fn(), listRestores: vi.fn(), retryRestore: vi.fn(), listProfiles: vi.fn(),
}))
import * as api from '@/api/sync'
import { ApiError } from '@/api/client'
import { useSyncStore } from '@/stores/sync'
import { defineComponent, h, nextTick, ref } from 'vue'

const group = {
  id: 'g', name: 'Downstairs', session_id: null, restore_pending: null, error: null,
  members: [
    { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 120 },
    { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null },
  ],
}

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

describe('SyncView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(api.listRestores).mockResolvedValue([])
    vi.mocked(api.listProfiles).mockResolvedValue([])
  })

  it('loads the group of the route and shows its tabs and members', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    const w = await mountView()
    expect(api.getSyncGroup).toHaveBeenCalledWith('g')
    expect(w.get('h1').text()).toContain('Downstairs')
    expect(w.findAll('[role="tab"]').map((t) => t.text())).toEqual(['Group', 'Manual', 'Measure'])
    expect(w.text()).toContain('Kitchen')
    expect(w.text()).toContain('120 ms')
    expect(w.text()).toContain('TV')
    expect(w.find('[aria-live]').exists()).toBe(true)
  })

  it('offers a retry when the group could not be restored after a restart', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
    vi.mocked(api.listRestores).mockResolvedValue([{ session_id: 'p1', group_id: 'g', error: 'ma_unavailable' }] as never)
    vi.mocked(api.retryRestore).mockResolvedValue({ session_id: 'p1', group_id: 'g', error: null } as never)
    const w = await mountView()
    expect(w.text()).toContain('Could not restore the group')
    await w.findAll('button').find((b) => b.text() === 'Retry')!.trigger('click')
    await flushPromises()
    expect(api.retryRestore).toHaveBeenCalledWith('g')
  })

  it('says when the group members could not be read', async () => {
    vi.mocked(api.getSyncGroup).mockResolvedValue({ ...group, members: [], error: 'ma_unavailable' } as never)
    const w = await mountView()
    expect(w.get('[role="alert"]').text()).toContain('ma_unavailable')
  })

  it('says why when the group itself cannot be loaded', async () => {
    vi.mocked(api.getSyncGroup).mockRejectedValue(new ApiError({ status: 404, code: 'not_found' }))
    const w = await mountView()
    expect(w.get('[role="alert"]').text()).toContain('not_found')
  })
})

describe('SyncView group tab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(api.listRestores).mockResolvedValue([])
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.getSyncGroup).mockResolvedValue(group as never)
  })
  afterEach(() => { vi.useRealTimers() })

  it('shows member origins and the profiles panel', async () => {
    const w = await mountView()
    expect(w.findAll('[data-member]')).toHaveLength(2)
    expect(w.text()).toContain('Cannot be adjusted')
    expect(w.find('[data-save-current]').exists()).toBe(true)
    expect(api.listProfiles).toHaveBeenCalledWith('g')
  })

  it('offers the two ways to tune with when to choose each, and opens the chosen tab', async () => {
    const w = await mountView()
    expect(w.findAll('[data-action]').map((b) => b.attributes('data-action'))).toEqual(['manual', 'semi'])
    expect(w.text()).toContain('No microphone needed. Best for small fixes you can hear.')
    expect(w.text()).toContain('About a minute per speaker.')
    expect(w.text()).not.toContain('Measure and apply')
    await w.get('[data-action="manual"]').trigger('click')
    expect(w.get('[role="tab"][aria-selected="true"]').text()).toBe('Manual')
    await w.get('[role="tab"]').trigger('click')
    await w.get('[data-action="semi"]').trigger('click')
    expect(w.get('[role="tab"][aria-selected="true"]').text()).toBe('Measure')
    await w.get('[data-switch-manual]').trigger('click')
    expect(w.get('[role="tab"][aria-selected="true"]').text()).toBe('Manual')
  })

  it('mounts the measurement wizard only while the Measure tab is open', async () => {
    const w = await mountView()
    expect(w.findComponent(MeasureWizard).exists()).toBe(false)
    await w.get('[data-action="semi"]').trigger('click')
    expect(w.findComponent(MeasureWizard).props('groupId')).toBe('g')
    expect(w.text()).toContain('Nothing else plays while you measure')
    await w.get('[role="tab"]').trigger('click')
    expect(w.findComponent(MeasureWizard).exists()).toBe(false)
  })

  it('polls a pending restore until it clears, then reads the group again', async () => {
    vi.useFakeTimers()
    vi.mocked(api.listRestores)
      .mockResolvedValueOnce([{ session_id: 'p1', group_id: 'g', error: null }] as never)
      .mockResolvedValueOnce([{ session_id: 'p1', group_id: 'g', error: 'ma_unavailable' }] as never)
      .mockResolvedValue([])
    const w = await mountView()
    expect(api.listRestores).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.listRestores).toHaveBeenCalledTimes(2)
    expect(w.text()).toContain('Could not restore the group')
    const reads = vi.mocked(api.getSyncGroup).mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.listRestores).toHaveBeenCalledTimes(3)
    expect(w.text()).not.toContain('Could not restore the group')
    expect(vi.mocked(api.getSyncGroup).mock.calls.length).toBeGreaterThan(reads)
    await vi.advanceTimersByTimeAsync(20000)
    expect(api.listRestores).toHaveBeenCalledTimes(3)
  })

  it('stops polling when the view unmounts', async () => {
    vi.useFakeTimers()
    vi.mocked(api.listRestores).mockResolvedValue([{ session_id: 'p1', group_id: 'g', error: null }] as never)
    const w = await mountView()
    w.unmount()
    await vi.advanceTimersByTimeAsync(20000)
    expect(api.listRestores).toHaveBeenCalledTimes(1)
  })

  it('polls a restore that was already pending before the view opened', async () => {
    vi.useFakeTimers()
    const pending = [{ session_id: 'p1', group_id: 'g', error: null }]
    useSyncStore().restores = pending as never
    vi.mocked(api.listRestores).mockResolvedValue(pending as never)
    await mountView()
    expect(api.listRestores).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.listRestores).toHaveBeenCalledTimes(2)
  })

  it('does not poll again when the view unmounts during a poll', async () => {
    vi.useFakeTimers()
    const pending = [{ session_id: 'p1', group_id: 'g', error: null }]
    let answer: (v: unknown) => void = () => {}
    vi.mocked(api.listRestores)
      .mockResolvedValueOnce(pending as never)
      .mockImplementationOnce(() => new Promise((r) => { answer = r }) as never)
      .mockResolvedValue(pending as never)
    // The view goes away while the app (and its route) stays, as when the page is left in place.
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/groups', name: 'groups', component: { template: '<div />' } },
        { path: '/sync/:groupId', name: 'sync', component: SyncView },
      ],
    })
    await router.push('/sync/g')
    const show = ref(true)
    const host = defineComponent({ setup: () => () => (show.value ? h(SyncView) : null) })
    mount(host, { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router] } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.listRestores).toHaveBeenCalledTimes(2)
    show.value = false
    await nextTick()
    answer(pending)
    await vi.advanceTimersByTimeAsync(20000)
    expect(api.listRestores).toHaveBeenCalledTimes(2)
  })

  it('refreshes the restores after a retry', async () => {
    vi.mocked(api.listRestores).mockResolvedValueOnce([{ session_id: 'p1', group_id: 'g', error: 'ma_unavailable' }] as never).mockResolvedValue([])
    vi.mocked(api.retryRestore).mockResolvedValue({ session_id: 'p1', group_id: 'g', error: 'ma_unavailable' } as never)
    const w = await mountView()
    await w.get('[data-retry]').trigger('click')
    await flushPromises()
    expect(api.retryRestore).toHaveBeenCalledWith('g')
    expect(api.listRestores).toHaveBeenCalledTimes(2)
    expect(w.text()).not.toContain('Could not restore the group')
  })
})
