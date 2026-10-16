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
  signInWithToken: vi.fn().mockResolvedValue({ url: 'http://ma:8095', username: 'u', message: '' }),
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

describe('MaLoginFlow — pasting a token', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('keeps the token form hidden until asked for', () => {
    const wrapper = mount(MaLoginFlow, { global: { plugins: [buildI18n()] } })
    expect(wrapper.find('[data-testid="ma-token-form"]').exists()).toBe(false)
    expect(wrapper.text()).toContain(en.ma.login.useToken)
  })

  it('connects with a pasted token and the server URL, then forgets the token', async () => {
    const { signInWithToken } = await import('@/api/ma')
    const { flushPromises } = await import('@vue/test-utils')
    const wrapper = mount(MaLoginFlow, { global: { plugins: [buildI18n()] } })
    await wrapper.findAll('input')[0]!.setValue('http://ma:8095')
    await wrapper.findAll('button').find((b) => b.text() === en.ma.login.useToken)!.trigger('click')

    const form = wrapper.find('[data-testid="ma-token-form"]')
    expect(form.exists()).toBe(true)
    expect(form.text()).toContain('Settings')
    const tokenInput = form.find('input')
    expect(tokenInput.attributes('type')).toBe('password')
    const submit = form.find('button[type="submit"]')
    expect(submit.attributes('disabled')).toBeDefined()

    await tokenInput.setValue('  pasted-token  ')
    await form.trigger('submit')
    await flushPromises()

    expect(signInWithToken).toHaveBeenCalledWith('http://ma:8095', 'pasted-token')
    expect((form.find('input').element as HTMLInputElement).value).toBe('')
  })

  it('shows why Music Assistant refused the token', async () => {
    const api = await import('@/api/ma')
    const { ApiError } = await import('@/api/client')
    const { flushPromises } = await import('@vue/test-utils')
    vi.mocked(api.signInWithToken).mockRejectedValueOnce(
      new ApiError({ status: 400, code: 'ma_token_invalid', title: 'Bad', detail: 'Music Assistant did not accept this token.' } as never),
    )
    const wrapper = mount(MaLoginFlow, { global: { plugins: [buildI18n()] } })
    await wrapper.findAll('button').find((b) => b.text() === en.ma.login.useToken)!.trigger('click')
    const form = wrapper.find('[data-testid="ma-token-form"]')
    await form.find('input').setValue('wrong')
    await form.trigger('submit')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('did not accept this token')
  })
})
