import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import SyncView from '@/views/SyncView.vue'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({
  getSyncGroup: vi.fn(), getSession: vi.fn(), listRestores: vi.fn(), retryRestore: vi.fn(), listProfiles: vi.fn(),
}))
import * as api from '@/api/sync'
import { ApiError } from '@/api/client'

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
