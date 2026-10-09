import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbEmptyState from '@/kit/SbEmptyState.vue'

describe('SbEmptyState', () => {
  it('renders title', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'No devices found' },
    })
    expect(wrapper.text()).toContain('No devices found')
  })

  it('renders title with correct styling', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
    })
    const title = wrapper.find('h3')
    expect(title.exists()).toBe(true)
    expect(title.classes()).toContain('text-lg')
    expect(title.classes()).toContain('font-semibold')
  })

  it('renders description when provided', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty', description: 'Try adding a device' },
    })
    expect(wrapper.text()).toContain('Try adding a device')
  })

  it('does not render description when not provided', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
    })
    const desc = wrapper.findAll('p')
    expect(desc).toHaveLength(0)
  })

  it('renders default icon SVG', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
    })
    const svg = wrapper.find('svg')
    expect(svg.exists()).toBe(true)
    expect(svg.attributes('aria-hidden')).toBe('true')
  })

  it('renders custom icon slot', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
      slots: { icon: '<span data-testid="custom-icon">🎵</span>' },
    })
    expect(wrapper.find('[data-testid="custom-icon"]').exists()).toBe(true)
    // Default SVG should not be rendered
    expect(wrapper.findAll('svg')).toHaveLength(0)
  })

  it('renders default slot content', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
      slots: { default: '<p>Extra info</p>' },
    })
    expect(wrapper.text()).toContain('Extra info')
  })

  it('renders action slot', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
      slots: { action: '<button>Add device</button>' },
    })
    expect(wrapper.text()).toContain('Add device')
    expect(wrapper.find('button').exists()).toBe(true)
  })

  it('does not render action wrapper when no action slot', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
    })
    // The action wrapper div should not appear
    const allText = wrapper.text()
    expect(allText).toBe('Empty')
  })

  it('has centered layout', () => {
    const wrapper = mount(SbEmptyState, {
      props: { title: 'Empty' },
    })
    const container = wrapper.find('.flex.flex-col')
    expect(container.exists()).toBe(true)
    expect(container.classes()).toContain('items-center')
    expect(container.classes()).toContain('text-center')
  })
})
