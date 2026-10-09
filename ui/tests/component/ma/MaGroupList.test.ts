import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaGroupList from '@/components/ma/MaGroupList.vue'
import en from '@/i18n/en.json'
import type { MaGroup } from '@/api/types'

let mockGroupsResult: MaGroup[] = []

vi.mock('@/api/ma', () => ({
  getGroups: () => Promise.resolve(mockGroupsResult),
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
    expect(wrapper.text()).toContain('2 members')
  })

  it('expands group on click to show members', async () => {
    mockGroupsResult = [
      {
        id: 'g1',
        name: 'Kitchen',
        members: [
          { id: 'p1', name: 'ENEBY', state: 'idle' },
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
