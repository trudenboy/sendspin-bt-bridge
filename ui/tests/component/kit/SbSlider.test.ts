import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbSlider from '@/kit/SbSlider.vue'

describe('SbSlider', () => {
  it('renders with default props', () => {
    const wrapper = mount(SbSlider)
    const input = wrapper.find('input[type="range"]')
    expect(input.exists()).toBe(true)
    expect(input.attributes('min')).toBe('0')
    expect(input.attributes('max')).toBe('100')
    expect(input.attributes('step')).toBe('1')
  })

  it('displays label', () => {
    const wrapper = mount(SbSlider, { props: { label: 'Volume' } })
    expect(wrapper.text()).toContain('Volume')
  })

  it('displays current value by default', () => {
    const wrapper = mount(SbSlider, { props: { modelValue: 42 } })
    expect(wrapper.text()).toContain('42')
  })

  it('hides value when showValue is false', () => {
    const wrapper = mount(SbSlider, { props: { modelValue: 42, showValue: false } })
    expect(wrapper.text()).not.toContain('42')
  })

  it('uses formatValue function', () => {
    const wrapper = mount(SbSlider, {
      props: { modelValue: 75, formatValue: (v: number) => `${v}%` },
    })
    expect(wrapper.text()).toContain('75%')
  })

  it('emits update:modelValue on input', async () => {
    const wrapper = mount(SbSlider, { props: { modelValue: 50 } })
    await wrapper.find('input').setValue('80')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([80])
  })

  it('respects min and max', () => {
    const wrapper = mount(SbSlider, { props: { min: 10, max: 200, step: 5 } })
    const input = wrapper.find('input')
    expect(input.attributes('min')).toBe('10')
    expect(input.attributes('max')).toBe('200')
    expect(input.attributes('step')).toBe('5')
  })

  it('applies disabled state', () => {
    const wrapper = mount(SbSlider, { props: { disabled: true } })
    expect(wrapper.find('input').attributes('disabled')).toBeDefined()
  })

  it('renders with gradient background style', () => {
    const wrapper = mount(SbSlider, { props: { modelValue: 50, min: 0, max: 100 } })
    const style = wrapper.find('input').attributes('style')
    expect(style).toContain('50%')
  })

  it('computes correct percentage for non-zero min', () => {
    const wrapper = mount(SbSlider, { props: { modelValue: 50, min: 0, max: 200 } })
    const style = wrapper.find('input').attributes('style') ?? ''
    expect(style).toContain('25%')
  })

  it('shows label and value together', () => {
    const wrapper = mount(SbSlider, {
      props: { label: 'Delay', modelValue: 300, showValue: true },
    })
    expect(wrapper.text()).toContain('Delay')
    expect(wrapper.text()).toContain('300')
  })
})
