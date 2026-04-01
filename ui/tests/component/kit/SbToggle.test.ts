import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbToggle from '@/kit/SbToggle.vue'

describe('SbToggle', () => {
  it('renders with default props', () => {
    const wrapper = mount(SbToggle)
    expect(wrapper.find('[role="switch"]').exists()).toBe(true)
    expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
  })

  it('reflects modelValue true', () => {
    const wrapper = mount(SbToggle, { props: { modelValue: true } })
    expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('true')
  })

  it('emits update:modelValue on click', async () => {
    const wrapper = mount(SbToggle, { props: { modelValue: false } })
    await wrapper.find('[role="switch"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([true])
  })

  it('toggles from true to false', async () => {
    const wrapper = mount(SbToggle, { props: { modelValue: true } })
    await wrapper.find('[role="switch"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])
  })

  it('renders label', () => {
    const wrapper = mount(SbToggle, { props: { label: 'Dark mode' } })
    expect(wrapper.find('label').text()).toBe('Dark mode')
  })

  it('has aria-label from label prop', () => {
    const wrapper = mount(SbToggle, { props: { label: 'Notify' } })
    expect(wrapper.find('[role="switch"]').attributes('aria-label')).toBe('Notify')
  })

  it('does not toggle when disabled', async () => {
    const wrapper = mount(SbToggle, { props: { modelValue: false, disabled: true } })
    await wrapper.find('[role="switch"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('toggles on Space keydown', async () => {
    const wrapper = mount(SbToggle, { props: { modelValue: false } })
    await wrapper.find('[role="switch"]').trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([true])
  })

  it('toggles on Enter keydown', async () => {
    const wrapper = mount(SbToggle, { props: { modelValue: false } })
    await wrapper.find('[role="switch"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([true])
  })

  it('renders in sm size', () => {
    const wrapper = mount(SbToggle, { props: { size: 'sm' } })
    const btn = wrapper.find('[role="switch"]')
    expect(btn.classes()).toContain('h-5')
    expect(btn.classes()).toContain('w-8')
  })

  it('renders in md size by default', () => {
    const wrapper = mount(SbToggle)
    const btn = wrapper.find('[role="switch"]')
    expect(btn.classes()).toContain('h-6')
    expect(btn.classes()).toContain('w-10')
  })
})
