import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import EventTimeline from '@/components/diagnostics/EventTimeline.vue'
import en from '@/i18n/en.json'

const mockTimeline = vi.fn()

vi.mock('@/api/diagnostics', () => ({
  getRecoveryTimeline: (...args: unknown[]) => mockTimeline(...args),
}))

const entry = (label: string, level: string, source: string) => ({
  at: '2024-01-01T10:00:00Z',
  level,
  source,
  label,
  summary: `${label} happened`,
})

function mountTimeline() {
  return mount(EventTimeline, {
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('EventTimeline', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockTimeline.mockResolvedValue({ entries: [] })
  })

  it('renders the search box', async () => {
    const wrapper = mountTimeline()
    await flushPromises()
    expect(wrapper.find('[role="searchbox"]').exists()).toBe(true)
  })

  it('shows an empty state without entries', async () => {
    const wrapper = mountTimeline()
    await flushPromises()
    expect(wrapper.text()).toContain('No events')
  })

  it('renders the recovery timeline entries', async () => {
    mockTimeline.mockResolvedValue({
      entries: [entry('reconnect', 'warning', 'Kitchen'), entry('daemon_crash', 'error', 'Office')],
    })
    const wrapper = mountTimeline()
    await flushPromises()
    expect(wrapper.findAll('[role="listitem"]').length).toBe(2)
    expect(wrapper.text()).toContain('reconnect')
    expect(wrapper.text()).toContain('Office: daemon_crash happened')
  })

  it('offers one filter chip per level present and filters by it', async () => {
    mockTimeline.mockResolvedValue({
      entries: [entry('a', 'warning', 'K'), entry('b', 'error', 'K'), entry('c', 'error', 'K')],
    })
    const wrapper = mountTimeline()
    await flushPromises()
    const chips = wrapper.findAll('button[aria-pressed]')
    expect(chips.map((c) => c.text())).toEqual(['error', 'warning'])
    await chips[0]!.trigger('click')
    expect(wrapper.findAll('[role="listitem"]').length).toBe(2)
  })
})
