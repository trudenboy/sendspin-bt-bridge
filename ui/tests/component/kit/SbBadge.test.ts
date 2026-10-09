import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbBadge from '@/kit/SbBadge.vue'

describe('SbBadge', () => {
  it('renders with default props and slot content', () => {
    const wrapper = mount(SbBadge, { slots: { default: 'Active' } })
    expect(wrapper.text()).toContain('Active')
  })

  it('applies neutral tone by default', () => {
    const wrapper = mount(SbBadge, { slots: { default: 'Tag' } })
    expect(wrapper.find('.tone-neutral').exists()).toBe(true)
  })

  it('applies the correct tone class for each tone', () => {
    const tones = ['success', 'warning', 'error', 'info', 'neutral'] as const
    tones.forEach((tone) => {
      const wrapper = mount(SbBadge, { props: { tone }, slots: { default: 'X' } })
      expect(wrapper.find(`.tone-${tone}`).exists()).toBe(true)
    })
  })

  it('uses sm size classes', () => {
    const wrapper = mount(SbBadge, { props: { size: 'sm' }, slots: { default: 'S' } })
    const badge = wrapper.find('span')
    expect(badge.classes()).toContain('text-xs')
  })

  it('uses md size classes by default', () => {
    const wrapper = mount(SbBadge, { slots: { default: 'M' } })
    const badge = wrapper.find('span')
    expect(badge.classes()).toContain('text-sm')
  })

  it('shows dot indicator when dot=true', () => {
    const wrapper = mount(SbBadge, { props: { dot: true }, slots: { default: 'Dot' } })
    const dotEl = wrapper.find('[aria-hidden="true"]')
    expect(dotEl.exists()).toBe(true)
    expect(dotEl.classes()).toContain('rounded-full')
  })

  it('hides dot indicator by default', () => {
    const wrapper = mount(SbBadge, { slots: { default: 'No dot' } })
    expect(wrapper.find('[aria-hidden="true"]').exists()).toBe(false)
  })

  it('shows remove button when removable', () => {
    const wrapper = mount(SbBadge, { props: { removable: true }, slots: { default: 'X' } })
    expect(wrapper.find('button[aria-label="Remove"]').exists()).toBe(true)
  })

  it('hides remove button by default', () => {
    const wrapper = mount(SbBadge, { slots: { default: 'X' } })
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('emits remove when remove button is clicked', async () => {
    const wrapper = mount(SbBadge, { props: { removable: true }, slots: { default: 'X' } })
    await wrapper.find('button[aria-label="Remove"]').trigger('click')
    expect(wrapper.emitted('remove')).toHaveLength(1)
  })

  it('applies dot color matching the tone', () => {
    const wrapper = mount(SbBadge, { props: { tone: 'error', dot: true }, slots: { default: 'Err' } })
    const dotEl = wrapper.find('[aria-hidden="true"]')
    expect(dotEl.classes()).toContain('bg-error')
  })

  it('has pill shape with radius-badge', () => {
    const wrapper = mount(SbBadge, { slots: { default: 'Pill' } })
    const badge = wrapper.find('span')
    expect(badge.classes()).toContain('rounded-[--radius-badge]')
  })
})
