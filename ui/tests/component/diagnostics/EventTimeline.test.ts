import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import EventTimeline from '@/components/diagnostics/EventTimeline.vue'
import { useEventStore } from '@/stores/events'
import en from '@/i18n/en.json'

const mockQueryEvents = vi.fn().mockResolvedValue([])
const mockGetEventStats = vi.fn().mockResolvedValue({
  total_events: 0,
  buffer_capacity: 1000,
  unique_subjects: 0,
  event_types: [],
})

vi.mock('@/api/events', () => ({
  queryEvents: (...args: unknown[]) => mockQueryEvents(...args),
  getEventStats: (...args: unknown[]) => mockGetEventStats(...args),
}))

function buildI18n() {
  return createI18n({ legacy: false, locale: 'en', messages: { en } })
}

describe('EventTimeline', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockQueryEvents.mockResolvedValue([])
    mockGetEventStats.mockResolvedValue({
      total_events: 0,
      buffer_capacity: 1000,
      unique_subjects: 0,
      event_types: [],
    })
  })

  it('renders filter bar', async () => {
    const wrapper = mount(EventTimeline, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    expect(wrapper.find('[role="searchbox"]').exists()).toBe(true)
  })

  it('shows empty state when no events', async () => {
    mockQueryEvents.mockResolvedValue([])

    const wrapper = mount(EventTimeline, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('No events')
  })

  it('renders timeline with events', async () => {
    const events = [
      {
        event_type: 'connection',
        subject_id: 'device-1',
        category: 'connection',
        payload: {},
        at: '2024-01-01T10:00:00Z',
      },
      {
        event_type: 'playback',
        subject_id: 'device-2',
        category: 'playback',
        payload: {},
        at: '2024-01-01T10:05:00Z',
      },
    ]
    mockQueryEvents.mockResolvedValue(events)

    const wrapper = mount(EventTimeline, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()

    expect(wrapper.find('[role="list"]').exists()).toBe(true)
    expect(wrapper.findAll('[role="listitem"]').length).toBe(2)
    expect(wrapper.text()).toContain('connection')
    expect(wrapper.text()).toContain('playback')
  })

  it('renders filter chips from stats', async () => {
    mockGetEventStats.mockResolvedValue({
      total_events: 10,
      buffer_capacity: 1000,
      unique_subjects: 2,
      event_types: ['connection', 'error'],
    })

    const wrapper = mount(EventTimeline, {
      global: { plugins: [buildI18n()] },
    })
    await flushPromises()

    const chips = wrapper.findAll('button[aria-pressed]')
    expect(chips.length).toBe(2)
    expect(chips[0].text()).toBe('connection')
    expect(chips[1].text()).toBe('error')
  })
})
