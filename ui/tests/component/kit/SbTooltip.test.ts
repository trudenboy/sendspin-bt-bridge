import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import SbTooltip from '@/kit/SbTooltip.vue'

describe('SbTooltip', () => {
  it('renders trigger slot content', () => {
    const wrapper = mount(SbTooltip, {
      props: { content: 'Tip' },
      slots: { default: '<span>Hover me</span>' },
    })
    expect(wrapper.text()).toContain('Hover me')
  })

  it('does not show tooltip by default', () => {
    const wrapper = mount(SbTooltip, {
      props: { content: 'Tip' },
      slots: { default: 'Trigger' },
    })
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)
  })

  it('shows tooltip after mouseenter and delay', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Hello', delay: 100 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('mouseenter')
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)

    vi.advanceTimersByTime(100)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(true)
    expect(wrapper.find('[role="tooltip"]').text()).toBe('Hello')

    vi.useRealTimers()
  })

  it('hides tooltip on mouseleave', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Bye', delay: 0 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('mouseenter')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(true)

    await wrapper.find('.relative').trigger('mouseleave')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)

    vi.useRealTimers()
  })

  it('shows tooltip on focusin', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Focus tip', delay: 0 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('focusin')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(true)

    vi.useRealTimers()
  })

  it('hides tooltip on focusout', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Gone', delay: 0 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('focusin')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()

    await wrapper.find('.relative').trigger('focusout')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false)

    vi.useRealTimers()
  })

  it('tooltip has role="tooltip"', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Tip', delay: 0 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('mouseenter')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()

    const tooltip = wrapper.find('[role="tooltip"]')
    expect(tooltip.exists()).toBe(true)

    vi.useRealTimers()
  })

  it('sets aria-describedby on trigger when visible', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Desc', delay: 0 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('mouseenter')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()

    const tooltip = wrapper.find('[role="tooltip"]')
    const triggerId = tooltip.attributes('id')
    const triggerSpan = wrapper.find(`[aria-describedby="${triggerId}"]`)
    expect(triggerSpan.exists()).toBe(true)

    vi.useRealTimers()
  })

  it('applies z-50 class to tooltip', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Z', delay: 0 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('mouseenter')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="tooltip"]').classes()).toContain('z-50')

    vi.useRealTimers()
  })

  it('renders position top by default', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Top', delay: 0 },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('mouseenter')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()

    const tooltip = wrapper.find('[role="tooltip"]')
    expect(tooltip.classes()).toContain('bottom-full')

    vi.useRealTimers()
  })

  it('renders position bottom', async () => {
    vi.useFakeTimers()
    const wrapper = mount(SbTooltip, {
      props: { content: 'Bot', delay: 0, position: 'bottom' },
      slots: { default: 'Trigger' },
    })

    await wrapper.find('.relative').trigger('mouseenter')
    vi.advanceTimersByTime(0)
    await wrapper.vm.$nextTick()

    const tooltip = wrapper.find('[role="tooltip"]')
    expect(tooltip.classes()).toContain('top-full')

    vi.useRealTimers()
  })
})
