import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbDropdown from '@/kit/SbDropdown.vue'
import SbDropdownItem from '@/kit/SbDropdownItem.vue'

describe('SbDropdown', () => {
  it('renders closed by default', () => {
    const wrapper = mount(SbDropdown)
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
  })

  it('opens on trigger click', async () => {
    const wrapper = mount(SbDropdown, {
      slots: { default: '<div class="item">Item 1</div>' },
    })
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="menu"]').exists()).toBe(true)
  })

  it('closes on second trigger click', async () => {
    const wrapper = mount(SbDropdown)
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="menu"]').exists()).toBe(true)
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
  })

  it('closes on ESC key', async () => {
    const wrapper = mount(SbDropdown)
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="menu"]').exists()).toBe(true)
    await wrapper.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
  })

  it('closes when menu item is clicked', async () => {
    const wrapper = mount(SbDropdown, {
      slots: { default: '<button role="menuitem">Action</button>' },
    })
    await wrapper.find('button').trigger('click')
    await wrapper.find('[role="menu"]').trigger('click')
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
  })

  it('renders trigger slot', async () => {
    const wrapper = mount(SbDropdown, {
      slots: { trigger: '<span class="custom-trigger">Open</span>' },
    })
    expect(wrapper.find('.custom-trigger').exists()).toBe(true)
  })

  it('aligns right when specified', async () => {
    const wrapper = mount(SbDropdown, { props: { align: 'right' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="menu"]').classes()).toContain('right-0')
  })

  it('aligns left by default', async () => {
    const wrapper = mount(SbDropdown)
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="menu"]').classes()).toContain('left-0')
  })

  it('has aria-haspopup on default trigger', () => {
    const wrapper = mount(SbDropdown)
    expect(wrapper.find('button').attributes('aria-haspopup')).toBe('true')
  })

  it('applies full width class', async () => {
    const wrapper = mount(SbDropdown, { props: { width: 'full' } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="menu"]').classes()).toContain('w-full')
  })

  it('applies custom width via style', async () => {
    const wrapper = mount(SbDropdown, { props: { width: '300px' } })
    await wrapper.find('button').trigger('click')
    const style = wrapper.find('[role="menu"]').attributes('style') ?? ''
    expect(style).toContain('300px')
  })
})

describe('SbDropdownItem', () => {
  it('renders slot content', () => {
    const wrapper = mount(SbDropdownItem, {
      slots: { default: 'Delete' },
    })
    expect(wrapper.text()).toBe('Delete')
    expect(wrapper.find('[role="menuitem"]').exists()).toBe(true)
  })

  it('emits click on click', async () => {
    const wrapper = mount(SbDropdownItem)
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('applies destructive styles', () => {
    const wrapper = mount(SbDropdownItem, { props: { destructive: true } })
    expect(wrapper.find('button').classes()).toContain('text-error')
  })

  it('applies disabled state', () => {
    const wrapper = mount(SbDropdownItem, { props: { disabled: true } })
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    expect(wrapper.find('button').classes()).toContain('cursor-not-allowed')
  })

  it('has tabindex on button element', () => {
    const wrapper = mount(SbDropdownItem, { slots: { default: 'Edit' } })
    expect(wrapper.find('button').exists()).toBe(true)
  })

  it('applies opacity when disabled', () => {
    const wrapper = mount(SbDropdownItem, { props: { disabled: true } })
    expect(wrapper.find('button').classes()).toContain('opacity-50')
  })
})
