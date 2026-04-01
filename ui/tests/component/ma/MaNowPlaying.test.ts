import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaNowPlaying from '@/components/ma/MaNowPlaying.vue'
import en from '@/i18n/en.json'

vi.mock('@/api/ma', () => ({
  discoverMA: vi.fn().mockResolvedValue({ job_id: '1', status: 'running' }),
  getDiscoverResult: vi.fn().mockResolvedValue({ job_id: '1', status: 'completed', result: { servers: [] } }),
  getGroups: vi.fn().mockResolvedValue([]),
  getNowPlaying: vi.fn().mockResolvedValue({}),
  queueCmd: vi.fn().mockResolvedValue({ job_id: '1', status: 'completed' }),
  maLogin: vi.fn().mockResolvedValue({ success: true }),
  maReload: vi.fn().mockResolvedValue({ success: true }),
}))

function buildI18n() {
  return createI18n({ legacy: false, locale: 'en', messages: { en } })
}

describe('MaNowPlaying', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders with no track fallback', () => {
    const wrapper = mount(MaNowPlaying, {
      props: { groupId: 'g1' },
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Nothing playing')
  })

  it('renders playback control buttons', () => {
    const wrapper = mount(MaNowPlaying, {
      props: { groupId: 'g1' },
      global: { plugins: [buildI18n()] },
    })
    const buttons = wrapper.findAll('button')
    // prev, play/pause, next (+ possible artwork close button)
    const transportButtons = buttons.filter(
      (b) => ['Previous track', 'Play / Pause', 'Next track'].includes(b.attributes('aria-label') ?? '')
    )
    expect(transportButtons.length).toBe(3)
    expect(transportButtons[0].attributes('aria-label')).toBe('Previous track')
    expect(transportButtons[1].attributes('aria-label')).toBe('Play / Pause')
    expect(transportButtons[2].attributes('aria-label')).toBe('Next track')
  })

  it('renders artwork placeholder when no track', () => {
    const wrapper = mount(MaNowPlaying, {
      props: { groupId: 'g1' },
      global: { plugins: [buildI18n()] },
    })
    // Should render Music icon fallback (svg inside the artwork area)
    const artworkArea = wrapper.find('.h-16.w-16')
    expect(artworkArea.exists()).toBe(true)
  })
})
