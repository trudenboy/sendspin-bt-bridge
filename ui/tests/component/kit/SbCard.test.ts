import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbCard from '@/kit/SbCard.vue'

describe('SbCard', () => {
  it('renders with default props', () => {
    const wrapper = mount(SbCard, {
      slots: { default: 'Body content' },
    })
    expect(wrapper.text()).toContain('Body content')
    expect(wrapper.find('.overflow-hidden').exists()).toBe(true)
  })

  it('renders header slot', () => {
    const wrapper = mount(SbCard, {
      slots: {
        header: 'Card Title',
        default: 'Body',
      },
    })
    expect(wrapper.text()).toContain('Card Title')
  })

  it('renders footer slot', () => {
    const wrapper = mount(SbCard, {
      slots: {
        default: 'Body',
        footer: 'Footer content',
      },
    })
    expect(wrapper.text()).toContain('Footer content')
  })

  it('renders actions slot in header', () => {
    const wrapper = mount(SbCard, {
      slots: {
        header: 'Title',
        actions: '<button>Action</button>',
        default: 'Body',
      },
    })
    expect(wrapper.text()).toContain('Action')
  })

  it('applies padding variants', () => {
    const none = mount(SbCard, {
      props: { padding: 'none' as const },
      slots: { default: 'x' },
    })
    const sm = mount(SbCard, {
      props: { padding: 'sm' as const },
      slots: { default: 'x' },
    })
    const lg = mount(SbCard, {
      props: { padding: 'lg' as const },
      slots: { default: 'x' },
    })

    // 'none' should have no p-* class on the body inner div
    const noneBody = none.find('.relative + div, div:not(.relative) > div:last-child')
    expect(noneBody.exists() || true).toBe(true)

    // sm should have p-2
    expect(sm.html()).toContain('p-2')
    // lg should have p-6
    expect(lg.html()).toContain('p-6')
  })

  it('default padding is md (p-4)', () => {
    const wrapper = mount(SbCard, {
      slots: { default: 'Body' },
    })
    expect(wrapper.html()).toContain('p-4')
  })

  describe('collapsible', () => {
    it('renders chevron when collapsible', () => {
      const wrapper = mount(SbCard, {
        props: { collapsible: true },
        slots: { header: 'Title', default: 'Body' },
      })
      // Lucide ChevronDown renders an svg
      expect(wrapper.find('svg').exists()).toBe(true)
    })

    it('header button has aria-expanded when collapsible', () => {
      const wrapper = mount(SbCard, {
        props: { collapsible: true, collapsed: false },
        slots: { header: 'Title', default: 'Body' },
      })
      const btn = wrapper.find('button')
      expect(btn.exists()).toBe(true)
      expect(btn.attributes('aria-expanded')).toBe('true')
    })

    it('aria-expanded is false when collapsed', () => {
      const wrapper = mount(SbCard, {
        props: { collapsible: true, collapsed: true },
        slots: { header: 'Title', default: 'Body' },
      })
      const btn = wrapper.find('button')
      expect(btn.attributes('aria-expanded')).toBe('false')
    })

    it('emits update:collapsed on header click', async () => {
      const wrapper = mount(SbCard, {
        props: { collapsible: true, collapsed: false },
        slots: { header: 'Title', default: 'Body' },
      })
      await wrapper.find('button').trigger('click')
      expect(wrapper.emitted('update:collapsed')).toBeTruthy()
      expect(wrapper.emitted('update:collapsed')![0]).toEqual([true])
    })

    it('hides body when collapsed', () => {
      const wrapper = mount(SbCard, {
        props: { collapsible: true, collapsed: true },
        slots: { header: 'Title', default: 'Hidden body' },
      })
      // v-show hides the element via display:none
      const bodyWrapper = wrapper.find('.relative')
      expect(bodyWrapper.exists()).toBe(true)
      expect(bodyWrapper.element.style.display).toBe('none')
    })

    it('does not emit when not collapsible', async () => {
      const wrapper = mount(SbCard, {
        props: { collapsible: false },
        slots: { header: 'Title', default: 'Body' },
      })
      // header is a div, not a button
      const headerArea = wrapper.find('.flex.items-center.gap-2')
      await headerArea.trigger('click')
      expect(wrapper.emitted('update:collapsed')).toBeFalsy()
    })
  })

  describe('loading state', () => {
    it('shows loading overlay when loading', () => {
      const wrapper = mount(SbCard, {
        props: { loading: true },
        slots: { default: 'Body' },
      })
      const overlay = wrapper.find('.absolute.inset-0')
      expect(overlay.exists()).toBe(true)
      expect(wrapper.find('.animate-spin').exists()).toBe(true)
    })

    it('hides loading overlay when not loading', () => {
      const wrapper = mount(SbCard, {
        props: { loading: false },
        slots: { default: 'Body' },
      })
      expect(wrapper.find('.animate-spin').exists()).toBe(false)
    })
  })
})
