import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbInput from '@/kit/SbInput.vue'

describe('SbInput', () => {
  it('renders with default props', () => {
    const wrapper = mount(SbInput)
    expect(wrapper.find('input').exists()).toBe(true)
    expect(wrapper.find('input').attributes('type')).toBe('text')
  })

  it('renders label when provided', () => {
    const wrapper = mount(SbInput, { props: { label: 'Email' } })
    expect(wrapper.find('label').text()).toBe('Email')
  })

  it('renders required indicator', () => {
    const wrapper = mount(SbInput, { props: { label: 'Name', required: true } })
    expect(wrapper.find('label').text()).toContain('*')
    expect(wrapper.find('input').attributes('aria-required')).toBe('true')
  })

  it('supports v-model', async () => {
    const wrapper = mount(SbInput, { props: { modelValue: 'hello' } })
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe('hello')

    await wrapper.find('input').setValue('world')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['world'])
  })

  it('renders error message', () => {
    const wrapper = mount(SbInput, { props: { error: 'Required field' } })
    expect(wrapper.find('[role="alert"]').text()).toBe('Required field')
    expect(wrapper.find('input').attributes('aria-invalid')).toBe('true')
  })

  it('renders hint when no error', () => {
    const wrapper = mount(SbInput, { props: { hint: 'Enter your email' } })
    expect(wrapper.text()).toContain('Enter your email')
  })

  it('error takes priority over hint', () => {
    const wrapper = mount(SbInput, { props: { error: 'Bad', hint: 'Help' } })
    expect(wrapper.text()).toContain('Bad')
    expect(wrapper.text()).not.toContain('Help')
  })

  it('renders placeholder', () => {
    const wrapper = mount(SbInput, { props: { placeholder: 'Type here' } })
    expect(wrapper.find('input').attributes('placeholder')).toBe('Type here')
  })

  it('applies disabled state', () => {
    const wrapper = mount(SbInput, { props: { disabled: true } })
    expect(wrapper.find('input').attributes('disabled')).toBeDefined()
  })

  it('supports different input types', () => {
    const wrapper = mount(SbInput, { props: { type: 'password' } })
    expect(wrapper.find('input').attributes('type')).toBe('password')
  })

  it('renders prefix slot', () => {
    const wrapper = mount(SbInput, {
      slots: { prefix: '<span class="test-prefix">@</span>' },
    })
    expect(wrapper.find('.test-prefix').exists()).toBe(true)
  })

  it('renders suffix slot', () => {
    const wrapper = mount(SbInput, {
      slots: { suffix: '<span class="test-suffix">.com</span>' },
    })
    expect(wrapper.find('.test-suffix').exists()).toBe(true)
  })

  it('has aria-describedby when error present', () => {
    const wrapper = mount(SbInput, { props: { error: 'Oops' } })
    const input = wrapper.find('input')
    const descId = input.attributes('aria-describedby')
    expect(descId).toBeTruthy()
    expect(wrapper.find(`#${descId}`).text()).toBe('Oops')
  })
})
