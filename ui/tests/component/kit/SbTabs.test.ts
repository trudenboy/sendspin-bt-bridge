import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbTabs from '@/kit/SbTabs.vue'

const baseTabs = [
  { id: 'general', label: 'General' },
  { id: 'advanced', label: 'Advanced' },
  { id: 'about', label: 'About' },
]

describe('SbTabs', () => {
  it('renders all tab buttons', () => {
    const wrapper = mount(SbTabs, {
      props: { tabs: baseTabs },
    })
    const buttons = wrapper.findAll('[role="tab"]')
    expect(buttons).toHaveLength(3)
    expect(buttons[0].text()).toBe('General')
    expect(buttons[1].text()).toBe('Advanced')
    expect(buttons[2].text()).toBe('About')
  })

  it('has correct ARIA tablist role', () => {
    const wrapper = mount(SbTabs, {
      props: { tabs: baseTabs },
    })
    expect(wrapper.find('[role="tablist"]').exists()).toBe(true)
  })

  it('renders tab panels with correct ARIA roles', () => {
    const wrapper = mount(SbTabs, {
      props: { tabs: baseTabs },
      slots: {
        general: 'General content',
        advanced: 'Advanced content',
      },
    })
    const panels = wrapper.findAll('[role="tabpanel"]')
    expect(panels).toHaveLength(3)
    expect(panels[0].attributes('aria-labelledby')).toBe('tab-general')
  })

  it('first tab is active by default when no modelValue', () => {
    const wrapper = mount(SbTabs, {
      props: { tabs: baseTabs },
      slots: { general: 'General content' },
    })
    const firstTab = wrapper.find('[role="tab"]')
    expect(firstTab.attributes('aria-selected')).toBe('true')
  })

  it('shows panel content for active tab', () => {
    const wrapper = mount(SbTabs, {
      props: { tabs: baseTabs, modelValue: 'general' },
      slots: {
        general: 'General content',
        advanced: 'Advanced content',
      },
    })
    const generalPanel = wrapper.find('#panel-general')
    const advancedPanel = wrapper.find('#panel-advanced')
    expect(generalPanel.element.style.display).not.toBe('none')
    expect(advancedPanel.element.style.display).toBe('none')
  })

  it('emits update:modelValue on tab click', async () => {
    const wrapper = mount(SbTabs, {
      props: { tabs: baseTabs, modelValue: 'general' },
    })
    const tabs = wrapper.findAll('[role="tab"]')
    await tabs[1].trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual(['advanced'])
  })

  it('renders badge next to tab label', () => {
    const tabsWithBadge = [
      { id: 'inbox', label: 'Inbox', badge: 5 },
      { id: 'sent', label: 'Sent' },
    ]
    const wrapper = mount(SbTabs, {
      props: { tabs: tabsWithBadge },
    })
    expect(wrapper.text()).toContain('5')
  })

  it('disables tabs with disabled flag', () => {
    const tabs = [
      { id: 'a', label: 'A' },
      { id: 'b', label: 'B', disabled: true },
    ]
    const wrapper = mount(SbTabs, {
      props: { tabs },
    })
    const disabledTab = wrapper.findAll('[role="tab"]')[1]
    expect(disabledTab.attributes('disabled')).toBeDefined()
    expect(disabledTab.classes()).toContain('opacity-50')
  })

  it('does not switch to disabled tab on click', async () => {
    const tabs = [
      { id: 'a', label: 'A' },
      { id: 'b', label: 'B', disabled: true },
    ]
    const wrapper = mount(SbTabs, {
      props: { tabs, modelValue: 'a' },
    })
    const disabledTab = wrapper.findAll('[role="tab"]')[1]
    await disabledTab.trigger('click')
    // Should not emit update for disabled tab
    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeFalsy()
  })

  describe('keyboard navigation', () => {
    it('ArrowRight moves to next tab', async () => {
      const wrapper = mount(SbTabs, {
        props: { tabs: baseTabs, modelValue: 'general' },
      })
      const tablist = wrapper.find('[role="tablist"]')
      await tablist.trigger('keydown', { key: 'ArrowRight' })
      const emitted = wrapper.emitted('update:modelValue')
      expect(emitted).toBeTruthy()
      expect(emitted![0]).toEqual(['advanced'])
    })

    it('ArrowLeft moves to previous tab', async () => {
      const wrapper = mount(SbTabs, {
        props: { tabs: baseTabs, modelValue: 'advanced' },
      })
      const tablist = wrapper.find('[role="tablist"]')
      await tablist.trigger('keydown', { key: 'ArrowLeft' })
      const emitted = wrapper.emitted('update:modelValue')
      expect(emitted).toBeTruthy()
      expect(emitted![0]).toEqual(['general'])
    })

    it('Home moves to first tab', async () => {
      const wrapper = mount(SbTabs, {
        props: { tabs: baseTabs, modelValue: 'about' },
      })
      const tablist = wrapper.find('[role="tablist"]')
      await tablist.trigger('keydown', { key: 'Home' })
      const emitted = wrapper.emitted('update:modelValue')
      expect(emitted).toBeTruthy()
      expect(emitted![0]).toEqual(['general'])
    })

    it('End moves to last tab', async () => {
      const wrapper = mount(SbTabs, {
        props: { tabs: baseTabs, modelValue: 'general' },
      })
      const tablist = wrapper.find('[role="tablist"]')
      await tablist.trigger('keydown', { key: 'End' })
      const emitted = wrapper.emitted('update:modelValue')
      expect(emitted).toBeTruthy()
      expect(emitted![0]).toEqual(['about'])
    })

    it('ArrowRight wraps around to first tab', async () => {
      const wrapper = mount(SbTabs, {
        props: { tabs: baseTabs, modelValue: 'about' },
      })
      const tablist = wrapper.find('[role="tablist"]')
      await tablist.trigger('keydown', { key: 'ArrowRight' })
      const emitted = wrapper.emitted('update:modelValue')
      expect(emitted).toBeTruthy()
      expect(emitted![0]).toEqual(['general'])
    })
  })
})
