import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbSpinner from '@/kit/SbSpinner.vue'

describe('SbSpinner', () => {
  it('renders with default props', () => {
    const wrapper = mount(SbSpinner)
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
    expect(wrapper.find('svg').exists()).toBe(true)
  })

  it('has role="status" and default aria-label', () => {
    const wrapper = mount(SbSpinner)
    const el = wrapper.find('[role="status"]')
    expect(el.attributes('aria-label')).toBe('Loading')
  })

  it('accepts custom label prop', () => {
    const wrapper = mount(SbSpinner, { props: { label: 'Please wait' } })
    expect(wrapper.find('[role="status"]').attributes('aria-label')).toBe('Please wait')
  })

  it('renders visually hidden text', () => {
    const wrapper = mount(SbSpinner, { props: { label: 'Loading data' } })
    const srOnly = wrapper.find('.sr-only')
    expect(srOnly.exists()).toBe(true)
    expect(srOnly.text()).toBe('Loading data')
  })

  it('uses correct SVG size for sm', () => {
    const wrapper = mount(SbSpinner, { props: { size: 'sm' } })
    const svg = wrapper.find('svg')
    expect(svg.attributes('width')).toBe('16')
    expect(svg.attributes('height')).toBe('16')
  })

  it('uses correct SVG size for md (default)', () => {
    const wrapper = mount(SbSpinner)
    const svg = wrapper.find('svg')
    expect(svg.attributes('width')).toBe('24')
    expect(svg.attributes('height')).toBe('24')
  })

  it('uses correct SVG size for lg', () => {
    const wrapper = mount(SbSpinner, { props: { size: 'lg' } })
    const svg = wrapper.find('svg')
    expect(svg.attributes('width')).toBe('40')
    expect(svg.attributes('height')).toBe('40')
  })

  it('has animate-spin class on SVG', () => {
    const wrapper = mount(SbSpinner)
    expect(wrapper.find('svg').classes()).toContain('animate-spin')
  })
})
