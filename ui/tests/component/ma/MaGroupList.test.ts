import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaGroupList from '@/components/ma/MaGroupList.vue'
import en from '@/i18n/en.json'
import type { SyncGroup } from '@/api/types'

let mockGroupsResult: SyncGroup[] = []

vi.mock('@/api/ma', () => ({
  discoverMA: vi.fn().mockResolvedValue({ job_id: '1', status: 'running' }),
  getDiscoverResult: vi.fn().mockResolvedValue({ job_id: '1', status: 'completed', result: { servers: [] } }),
  getGroups: () => Promise.resolve(mockGroupsResult),
  getNowPlaying: vi.fn().mockResolvedValue({}),
  queueCmd: vi.fn().mockResolvedValue({ job_id: '1', status: 'completed' }),
  maLogin: vi.fn().mockResolvedValue({ success: true }),
  maReload: vi.fn().mockResolvedValue({ success: true }),
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
        group_id: 'g1',
        group_name: 'Living Room',
        members: [
          { player_id: 'p1', player_name: 'Speaker 1', state: 'STREAMING' },
          { player_id: 'p2', player_name: 'Speaker 2', state: 'READY' },
        ],
      },
    ]

    const wrapper = mount(MaGroupList, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Living Room')
    expect(wrapper.text()).toContain('2 members')
  })

  it('expands group on click to show members', async () => {
    mockGroupsResult = [
      {
        group_id: 'g1',
        group_name: 'Kitchen',
        members: [
          { player_id: 'p1', player_name: 'ENEBY', state: 'READY' },
        ],
      },
    ]

    const wrapper = mount(MaGroupList, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    await wrapper.find('button[aria-expanded]').trigger('click')
    expect(wrapper.text()).toContain('ENEBY')
  })
})
