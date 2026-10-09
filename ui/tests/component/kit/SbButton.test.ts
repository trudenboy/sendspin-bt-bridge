import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbButton from '@/kit/SbButton.vue'

describe('SbButton', () => {
  it('renders as a button element', () => {
    const wrapper = mount(SbButton, { slots: { default: 'Click' } })
    expect(wrapper.find('button').exists()).toBe(true)
  })

  it('renders slot content', () => {
    const wrapper = mount(SbButton, { slots: { default: 'Submit' } })
    expect(wrapper.text()).toContain('Submit')
  })

  it('applies primary variant classes by default', () => {
    const wrapper = mount(SbButton, { slots: { default: 'Go' } })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('bg-primary')
    expect(btn.classes()).toContain('text-white')
  })

  it('applies secondary variant classes', () => {
    const wrapper = mount(SbButton, {
      props: { variant: 'secondary' },
      slots: { default: 'Sec' },
    })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('bg-surface-secondary')
    expect(btn.classes()).toContain('text-text-primary')
  })

  it('applies ghost variant classes', () => {
    const wrapper = mount(SbButton, {
      props: { variant: 'ghost' },
      slots: { default: 'Ghost' },
    })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('bg-transparent')
    expect(btn.classes()).toContain('text-text-primary')
  })

  it('applies danger variant classes', () => {
    const wrapper = mount(SbButton, {
      props: { variant: 'danger' },
      slots: { default: 'Delete' },
    })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('bg-error')
    expect(btn.classes()).toContain('text-white')
  })

  it('applies sm size classes', () => {
    const wrapper = mount(SbButton, {
      props: { size: 'sm' },
      slots: { default: 'Sm' },
    })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('h-8')
    expect(btn.classes()).toContain('text-sm')
  })

  it('applies md size classes by default', () => {
    const wrapper = mount(SbButton, { slots: { default: 'Md' } })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('h-10')
    expect(btn.classes()).toContain('text-base')
  })

  it('applies lg size classes', () => {
    const wrapper = mount(SbButton, {
      props: { size: 'lg' },
      slots: { default: 'Lg' },
    })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('h-12')
    expect(btn.classes()).toContain('text-lg')
  })

  it('is disabled when disabled prop is true', () => {
    const wrapper = mount(SbButton, {
      props: { disabled: true },
      slots: { default: 'No' },
    })
    const btn = wrapper.find('button')
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.classes()).toContain('opacity-50')
  })

  it('is disabled when loading', () => {
    const wrapper = mount(SbButton, {
      props: { loading: true },
      slots: { default: 'Wait' },
    })
    const btn = wrapper.find('button')
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('shows spinner when loading', () => {
    const wrapper = mount(SbButton, {
      props: { loading: true },
      slots: { default: 'Wait' },
    })
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })

  it('has focus ring classes', () => {
    const wrapper = mount(SbButton, { slots: { default: 'F' } })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('focus-visible:ring-2')
    expect(btn.classes()).toContain('focus-visible:ring-primary')
  })

  it('renders icon-left slot', () => {
    const wrapper = mount(SbButton, {
      slots: {
        default: 'Label',
        'icon-left': '<span class="left-icon">←</span>',
      },
    })
    expect(wrapper.find('.left-icon').exists()).toBe(true)
  })

  it('renders icon-right slot', () => {
    const wrapper = mount(SbButton, {
      slots: {
        default: 'Label',
        'icon-right': '<span class="right-icon">→</span>',
      },
    })
    expect(wrapper.find('.right-icon').exists()).toBe(true)
  })

  it('hides icon slots when loading', () => {
    const wrapper = mount(SbButton, {
      props: { loading: true },
      slots: {
        default: 'Label',
        'icon-left': '<span class="left-icon">←</span>',
        'icon-right': '<span class="right-icon">→</span>',
      },
    })
    expect(wrapper.find('.left-icon').exists()).toBe(false)
    expect(wrapper.find('.right-icon').exists()).toBe(false)
  })

  it('applies square dimensions for icon-only button', () => {
    const wrapper = mount(SbButton, {
      props: { icon: true, size: 'md' },
      slots: { default: '★' },
    })
    const btn = wrapper.find('button')
    expect(btn.classes()).toContain('h-10')
    expect(btn.classes()).toContain('w-10')
  })

  it('has rounded-[--radius-button] class', () => {
    const wrapper = mount(SbButton, { slots: { default: 'R' } })
    expect(wrapper.find('button').classes()).toContain('rounded-[--radius-button]')
  })

  it('has transition class', () => {
    const wrapper = mount(SbButton, { slots: { default: 'T' } })
    expect(wrapper.find('button').classes()).toContain('transition-all')
  })
})
