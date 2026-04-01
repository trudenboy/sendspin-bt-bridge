import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaLoginFlow from '@/components/ma/MaLoginFlow.vue'
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

describe('MaLoginFlow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders login form', () => {
    const wrapper = mount(MaLoginFlow, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Connect to Music Assistant')
    expect(wrapper.text()).toContain('Step 1')
    expect(wrapper.text()).toContain('Step 2')
  })

  it('renders server URL input', () => {
    const wrapper = mount(MaLoginFlow, {
      global: { plugins: [buildI18n()] },
    })
    const inputs = wrapper.findAll('input')
    expect(inputs.length).toBeGreaterThanOrEqual(1)
  })

  it('renders discover button', () => {
    const wrapper = mount(MaLoginFlow, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Auto-Discover')
  })

  it('renders connect button', () => {
    const wrapper = mount(MaLoginFlow, {
      global: { plugins: [buildI18n()] },
    })
    expect(wrapper.text()).toContain('Connect')
  })

  it('connect button is disabled without token', () => {
    const wrapper = mount(MaLoginFlow, {
      global: { plugins: [buildI18n()] },
    })
    const connectBtn = wrapper.findAll('button').find((b) => b.text().includes('Connect'))
    expect(connectBtn?.attributes('disabled')).toBeDefined()
  })
})
