import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbStatusDot from '@/kit/SbStatusDot.vue'

describe('SbStatusDot', () => {
  it('renders with required status prop', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'ready' } })
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })

  it('shows correct aria-label for each status', () => {
    const statuses = ['streaming', 'ready', 'connecting', 'error', 'offline', 'standby'] as const
    const labels = ['Streaming', 'Ready', 'Connecting', 'Error', 'Offline', 'Standby']

    statuses.forEach((status, i) => {
      const wrapper = mount(SbStatusDot, { props: { status } })
      expect(wrapper.find('[role="status"]').attributes('aria-label')).toBe(labels[i])
    })
  })

  it('applies correct color class for streaming', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'streaming' } })
    const dot = wrapper.find('.rounded-full')
    expect(dot.classes()).toContain('bg-success')
  })

  it('applies correct color class for error', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'error' } })
    const dot = wrapper.find('.rounded-full')
    expect(dot.classes()).toContain('bg-error')
  })

  it('applies correct color class for offline', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'offline' } })
    const dot = wrapper.find('.rounded-full')
    expect(dot.classes()).toContain('bg-gray-400')
  })

  it('pulses by default for streaming', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'streaming' } })
    const pings = wrapper.findAll('.animate-ping')
    expect(pings.length).toBe(1)
  })

  it('pulses by default for connecting', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'connecting' } })
    const pings = wrapper.findAll('.animate-ping')
    expect(pings.length).toBe(1)
  })

  it('does not pulse by default for ready', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'ready' } })
    expect(wrapper.find('.animate-ping').exists()).toBe(false)
  })

  it('can force pulse on with pulse=true', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'ready', pulse: true } })
    expect(wrapper.find('.animate-ping').exists()).toBe(true)
  })

  it('can force pulse off with pulse=false', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'streaming', pulse: false } })
    expect(wrapper.find('.animate-ping').exists()).toBe(false)
  })

  it('uses sm size classes', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'ready', size: 'sm' } })
    const dot = wrapper.find('.rounded-full')
    expect(dot.classes()).toContain('h-2')
    expect(dot.classes()).toContain('w-2')
  })

  it('uses md size classes by default', () => {
    const wrapper = mount(SbStatusDot, { props: { status: 'ready' } })
    const dot = wrapper.find('.rounded-full')
    expect(dot.classes()).toContain('h-3')
    expect(dot.classes()).toContain('w-3')
  })
})
