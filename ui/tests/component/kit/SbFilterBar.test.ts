import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbFilterBar from '@/kit/SbFilterBar.vue'

const baseFilters = [
  { key: 'online', label: 'Online', active: true },
  { key: 'offline', label: 'Offline', active: false },
  { key: 'error', label: 'Error' },
]

describe('SbFilterBar', () => {
  it('renders search input', () => {
    const wrapper = mount(SbFilterBar)
    const input = wrapper.find('input[role="searchbox"]')
    expect(input.exists()).toBe(true)
  })

  it('renders with default placeholder', () => {
    const wrapper = mount(SbFilterBar)
    const input = wrapper.find('input')
    expect(input.attributes('placeholder')).toBe('Search...')
  })

  it('renders with custom placeholder', () => {
    const wrapper = mount(SbFilterBar, {
      props: { placeholder: 'Find devices...' },
    })
    const input = wrapper.find('input')
    expect(input.attributes('placeholder')).toBe('Find devices...')
  })

  it('search input has role="searchbox"', () => {
    const wrapper = mount(SbFilterBar)
    expect(wrapper.find('[role="searchbox"]').exists()).toBe(true)
  })

  it('renders filter chips', () => {
    const wrapper = mount(SbFilterBar, {
      props: { filters: baseFilters },
    })
    const buttons = wrapper.findAll('button[aria-pressed]')
    expect(buttons).toHaveLength(3)
    expect(buttons[0].text()).toBe('Online')
    expect(buttons[1].text()).toBe('Offline')
  })

  it('active filter has aria-pressed="true"', () => {
    const wrapper = mount(SbFilterBar, {
      props: { filters: baseFilters },
    })
    const buttons = wrapper.findAll('button[aria-pressed]')
    expect(buttons[0].attributes('aria-pressed')).toBe('true')
    expect(buttons[1].attributes('aria-pressed')).toBe('false')
  })

  it('active filter chip has primary styling', () => {
    const wrapper = mount(SbFilterBar, {
      props: { filters: baseFilters },
    })
    const activeChip = wrapper.findAll('button[aria-pressed]')[0]
    expect(activeChip.classes()).toContain('bg-primary')
    expect(activeChip.classes()).toContain('text-white')
  })

  it('inactive filter chip has secondary styling', () => {
    const wrapper = mount(SbFilterBar, {
      props: { filters: baseFilters },
    })
    const inactiveChip = wrapper.findAll('button[aria-pressed]')[1]
    expect(inactiveChip.classes()).toContain('bg-surface-secondary')
    expect(inactiveChip.classes()).toContain('text-text-secondary')
  })

  it('emits toggleFilter on chip click', async () => {
    const wrapper = mount(SbFilterBar, {
      props: { filters: baseFilters },
    })
    const buttons = wrapper.findAll('button[aria-pressed]')
    await buttons[1].trigger('click')

    expect(wrapper.emitted('toggleFilter')).toBeTruthy()
    expect(wrapper.emitted('toggleFilter')![0]).toEqual(['offline'])
  })

  it('emits update:modelValue on input', async () => {
    const wrapper = mount(SbFilterBar, {
      props: { modelValue: '' },
    })
    const input = wrapper.find('input')
    await input.setValue('test query')

    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([
      'test query',
    ])
  })

  it('shows clear button when search has value', () => {
    const wrapper = mount(SbFilterBar, {
      props: { modelValue: 'something' },
    })
    const clearBtn = wrapper.find('button[aria-label="Clear search"]')
    expect(clearBtn.exists()).toBe(true)
  })

  it('hides clear button when search is empty', () => {
    const wrapper = mount(SbFilterBar, {
      props: { modelValue: '' },
    })
    const clearBtn = wrapper.find('button[aria-label="Clear search"]')
    expect(clearBtn.exists()).toBe(false)
  })

  it('clears search on clear button click', async () => {
    const wrapper = mount(SbFilterBar, {
      props: { modelValue: 'query' },
    })
    const clearBtn = wrapper.find('button[aria-label="Clear search"]')
    await clearBtn.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([''])
  })

  it('renders without filters', () => {
    const wrapper = mount(SbFilterBar)
    const chips = wrapper.findAll('button[aria-pressed]')
    expect(chips).toHaveLength(0)
  })
})
