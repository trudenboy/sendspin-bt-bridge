import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaLoginFlow from '@/components/ma/MaLoginFlow.vue'
import en from '@/i18n/en.json'

vi.mock('@/api/ma', () => ({
  discoverMA: vi.fn().mockResolvedValue({ id: 'j', status: 'succeeded', result: { servers: [{ url: 'http://ma:8095' }] } }),
  getConnection: vi.fn().mockResolvedValue({ connected: true }),
  signInWithPassword: vi.fn().mockResolvedValue({ url: 'http://ma:8095', username: 'u', message: '' }),
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

  it('fills the server from discovery and signs in with the account', async () => {
    const { signInWithPassword } = await import('@/api/ma')
    const { flushPromises } = await import('@vue/test-utils')
    const wrapper = mount(MaLoginFlow, { global: { plugins: [buildI18n()] } })
    await wrapper.findAll('button').find((b) => b.text().includes('Auto-Discover'))!.trigger('click')
    await flushPromises()
    const inputs = wrapper.findAll('input')
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('http://ma:8095')
    await inputs[1]!.setValue('u')
    await inputs[2]!.setValue('p')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(signInWithPassword).toHaveBeenCalledWith('http://ma:8095', 'u', 'p')
  })
})
