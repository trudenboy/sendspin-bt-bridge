import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaGroupList from '@/components/ma/MaGroupList.vue'
import en from '@/i18n/en.json'
import type { MaGroup } from '@/api/types'
import { useBridgeStore } from '@/stores/bridge'
import { createRouter, createMemoryHistory } from 'vue-router'

let mockGroupsResult: MaGroup[] = []
const getGroupsSpy = vi.fn(() => Promise.resolve(mockGroupsResult))

vi.mock('@/api/ma', () => ({
  getGroups: () => getGroupsSpy(),
  getNowPlaying: vi.fn().mockResolvedValue({ connected: false }),
  refreshGroups: vi.fn().mockResolvedValue({ id: 'j', status: 'succeeded' }),
  queueCommand: vi.fn(),
}))

function buildI18n() {
  return createI18n({ legacy: false, locale: 'en', messages: { en } })
}

describe('MaGroupList', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGroupsResult = []
  })

  it('shows empty state when no groups', async () => {
    const wrapper = mount(MaGroupList, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('No Sync Groups')
  })

  it('renders discover button in empty state', async () => {
    const wrapper = mount(MaGroupList, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Discover Groups')
  })

  it('renders group cards', async () => {
    mockGroupsResult = [
      {
        id: 'g1',
        name: 'Living Room',
        members: [
          { id: 'p1', name: 'Speaker 1', state: 'playing' },
          { id: 'p2', name: 'Speaker 2', state: 'idle' },
        ],
      },
    ]

    const wrapper = mount(MaGroupList, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Living Room')
    expect(wrapper.findAll('li').map((li) => li.text())).toEqual(['Speaker 1', 'Speaker 2'])
  })

  it('shows members without the bridge suffix, the full name on hover', async () => {
    mockGroupsResult = [
      {
        id: 'g1',
        name: 'Kitchen',
        members: [
          { id: 'p1', name: 'ENEBY @ kitchen-bridge', state: 'idle' },
        ],
      },
    ]

    const wrapper = mount(MaGroupList, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    const member = wrapper.get('li')
    expect(member.text()).toBe('ENEBY')
    expect(member.find('[title]').attributes('title')).toBe('ENEBY @ kitchen-bridge')
  })

  it("colours each member by its Music Assistant state", async () => {
    mockGroupsResult = [
      {
        id: 'g1',
        name: 'Beta',
        members: [
          { id: 'a', name: 'Kitchen', state: 'playing', available: true },
          { id: 'b', name: 'Office', state: 'idle', available: true },
          { id: 'c', name: 'Porch', state: 'idle', available: false },
        ],
      },
    ]
    const wrapper = mount(MaGroupList, { global: { plugins: [buildI18n()] } })
    await flushPromises()
    const tones = wrapper.findAll('li > *').map((b) => b.classes().find((c) => c.startsWith('tone-')))
    expect(tones).toEqual(['tone-success', 'tone-neutral', 'tone-warning'])
  })

  it('refreshes the groups when the bridge reports a change', async () => {
    vi.useFakeTimers()
    try {
      mount(MaGroupList, { global: { plugins: [buildI18n()] } })
      await flushPromises()
      const before = getGroupsSpy.mock.calls.length
      useBridgeStore().snapshot = { devices: [], groups: [] } as never
      await vi.advanceTimersByTimeAsync(1000)
      expect(getGroupsSpy.mock.calls.length).toBe(before + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('opens group sync tuning from the group card', async () => {
    mockGroupsResult = [{ id: 'syncgroup_x', name: 'Downstairs', members: [] }]
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/sync/:groupId', name: 'sync', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(MaGroupList, { global: { plugins: [buildI18n(), router] } })
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text() === 'Sync')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('sync')
    expect(router.currentRoute.value.params.groupId).toBe('syncgroup_x')
  })

  it('offers to create a group in Music Assistant on the empty page', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    useBridgeStore().snapshot = { devices: [], groups: [], bridge: { ma_web_url: 'http://ma:8095/' } } as never
    const wrapper = mount(MaGroupList, { global: { plugins: [buildI18n()] } })
    await flushPromises()
    const btn = wrapper.findAll('button').find((b) => b.text().includes('Create a group in Music Assistant'))!
    expect(btn).toBeTruthy()
    await btn.trigger('click')
    expect(open.mock.calls[0]![0]).toContain('http://ma:8095/#/')
    open.mockRestore()
  })

  it('hides that action when the Music Assistant address is unknown', async () => {
    const wrapper = mount(MaGroupList, { global: { plugins: [buildI18n()] } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Create a group in Music Assistant')
  })
})
