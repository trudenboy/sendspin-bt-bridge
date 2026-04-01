import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbTimeline from '@/kit/SbTimeline.vue'

const sampleEvents = [
  { id: '1', timestamp: '10:00', title: 'Connected', type: 'success' as const },
  { id: '2', timestamp: '10:05', title: 'Streaming started', description: 'A2DP active', type: 'info' as const },
  { id: '3', timestamp: '10:10', title: 'Disconnected', type: 'error' as const },
  { id: '4', timestamp: '10:15', title: 'Reconnecting', type: 'warning' as const },
]

function mountTimeline(props: Record<string, unknown> = {}) {
  return mount(SbTimeline, {
    props: { events: sampleEvents, ...props },
  })
}

describe('SbTimeline', () => {
  describe('rendering', () => {
    it('renders all events', () => {
      const w = mountTimeline()
      expect(w.findAll('[role="listitem"]')).toHaveLength(4)
    })

    it('renders event titles', () => {
      const w = mountTimeline()
      expect(w.text()).toContain('Connected')
      expect(w.text()).toContain('Streaming started')
      expect(w.text()).toContain('Disconnected')
      expect(w.text()).toContain('Reconnecting')
    })

    it('renders timestamps', () => {
      const w = mountTimeline()
      expect(w.text()).toContain('10:00')
      expect(w.text()).toContain('10:05')
    })

    it('renders descriptions', () => {
      const w = mountTimeline()
      expect(w.text()).toContain('A2DP active')
    })

    it('renders the vertical line', () => {
      const w = mountTimeline()
      expect(w.find('.w-0\\.5').exists()).toBe(true)
    })
  })

  describe('ARIA', () => {
    it('has role="list" on container', () => {
      const w = mountTimeline()
      expect(w.find('[role="list"]').exists()).toBe(true)
    })

    it('has role="listitem" on each event', () => {
      const w = mountTimeline()
      const items = w.findAll('[role="listitem"]')
      expect(items).toHaveLength(4)
    })
  })

  describe('type colors', () => {
    it('applies success color to dot', () => {
      const w = mountTimeline({ events: [sampleEvents[0]] })
      const dot = w.find('[role="listitem"] span')
      expect(dot.classes()).toContain('bg-success')
    })

    it('applies error color to dot', () => {
      const w = mountTimeline({ events: [sampleEvents[2]] })
      const dot = w.find('[role="listitem"] span')
      expect(dot.classes()).toContain('bg-error')
    })

    it('applies info color by default', () => {
      const w = mountTimeline({
        events: [{ id: '5', timestamp: '11:00', title: 'Event' }],
      })
      const dot = w.find('[role="listitem"] span')
      expect(dot.classes()).toContain('bg-info')
    })
  })

  describe('maxItems', () => {
    it('shows all events when maxItems is 0', () => {
      const w = mountTimeline({ maxItems: 0 })
      expect(w.findAll('[role="listitem"]')).toHaveLength(4)
    })

    it('limits displayed events', () => {
      const w = mountTimeline({ maxItems: 2 })
      expect(w.findAll('[role="listitem"]')).toHaveLength(2)
    })

    it('shows "Show more" button when truncated', () => {
      const w = mountTimeline({ maxItems: 2 })
      expect(w.find('[data-testid="timeline-show-more"]').exists()).toBe(true)
      expect(w.text()).toContain('Show 2 more')
    })

    it('does not show "Show more" when all events visible', () => {
      const w = mountTimeline({ maxItems: 10 })
      expect(w.find('[data-testid="timeline-show-more"]').exists()).toBe(false)
    })

    it('expands to show all events on click', async () => {
      const w = mountTimeline({ maxItems: 2 })
      await w.find('[data-testid="timeline-show-more"]').trigger('click')
      expect(w.findAll('[role="listitem"]')).toHaveLength(4)
      expect(w.find('[data-testid="timeline-show-more"]').exists()).toBe(false)
    })
  })

  describe('icons', () => {
    it('renders custom icon in dot', () => {
      const w = mountTimeline({
        events: [{ id: '1', timestamp: '10:00', title: 'Test', icon: '🔊' }],
      })
      expect(w.text()).toContain('🔊')
    })
  })
})
