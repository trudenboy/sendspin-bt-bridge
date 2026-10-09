import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbSignalPath from '@/kit/SbSignalPath.vue'

const sampleSegments = [
  { id: 'src', label: 'Music Assistant', sublabel: 'Sendspin', status: 'active' as const },
  { id: 'bridge', label: 'Bridge', sublabel: 'Running', status: 'active' as const },
  { id: 'bt', label: 'Bluetooth', sublabel: 'A2DP', status: 'inactive' as const },
  { id: 'speaker', label: 'Speaker', status: 'error' as const },
]

function mountSignalPath(props: Record<string, unknown> = {}) {
  return mount(SbSignalPath, {
    props: { segments: sampleSegments, ...props },
  })
}

describe('SbSignalPath', () => {
  describe('rendering', () => {
    it('renders all segments', () => {
      const w = mountSignalPath()
      expect(w.text()).toContain('Music Assistant')
      expect(w.text()).toContain('Bridge')
      expect(w.text()).toContain('Bluetooth')
      expect(w.text()).toContain('Speaker')
    })

    it('renders sublabels', () => {
      const w = mountSignalPath()
      expect(w.text()).toContain('Sendspin')
      expect(w.text()).toContain('Running')
      expect(w.text()).toContain('A2DP')
    })

    it('renders arrow connectors between segments', () => {
      const w = mountSignalPath()
      const arrows = w.findAll('[aria-hidden="true"]')
      // 3 arrows between 4 segments
      expect(arrows.length).toBe(3)
    })
  })

  describe('ARIA', () => {
    it('has aria-label="Signal path"', () => {
      const w = mountSignalPath()
      expect(w.find('[aria-label="Signal path"]').exists()).toBe(true)
    })

    it('each segment has aria-label with status', () => {
      const w = mountSignalPath({
        segments: [{ id: 'a', label: 'Source', status: 'active' }],
      })
      const seg = w.find('[aria-label*="Source"]')
      expect(seg.exists()).toBe(true)
      expect(seg.attributes('aria-label')).toContain('active')
    })

    it('includes sublabel in aria-label', () => {
      const w = mountSignalPath({
        segments: [{ id: 'a', label: 'Source', sublabel: 'v2', status: 'active' }],
      })
      const seg = w.find('[aria-label*="Source"]')
      expect(seg.attributes('aria-label')).toContain('v2')
    })
  })

  describe('status styles', () => {
    it('applies active border class', () => {
      const w = mountSignalPath({
        segments: [{ id: 'a', label: 'Active', status: 'active' }],
      })
      const box = w.find('.rounded-lg')
      expect(box.classes()).toContain('border-success')
    })

    it('applies inactive border class', () => {
      const w = mountSignalPath({
        segments: [{ id: 'a', label: 'Inactive', status: 'inactive' }],
      })
      const box = w.find('.rounded-lg')
      expect(box.classes()).toContain('border-gray-300')
    })

    it('applies error border class', () => {
      const w = mountSignalPath({
        segments: [{ id: 'a', label: 'Error', status: 'error' }],
      })
      const box = w.find('.rounded-lg')
      expect(box.classes()).toContain('border-error')
    })

    it('defaults to inactive when no status', () => {
      const w = mountSignalPath({
        segments: [{ id: 'a', label: 'None' }],
      })
      const box = w.find('.rounded-lg')
      expect(box.classes()).toContain('border-gray-300')
    })
  })

  describe('direction', () => {
    it('defaults to horizontal', () => {
      const w = mountSignalPath()
      const container = w.find('[aria-label="Signal path"]')
      expect(container.classes()).toContain('flex-row')
    })

    it('uses flex-col for vertical', () => {
      const w = mountSignalPath({ direction: 'vertical' })
      const container = w.find('[aria-label="Signal path"]')
      expect(container.classes()).toContain('flex-col')
    })
  })

  describe('edge cases', () => {
    it('renders empty segments without errors', () => {
      const w = mountSignalPath({ segments: [] })
      expect(w.find('[aria-label="Signal path"]').exists()).toBe(true)
    })

    it('renders single segment without arrows', () => {
      const w = mountSignalPath({
        segments: [{ id: 'only', label: 'Only' }],
      })
      expect(w.findAll('[aria-hidden="true"]')).toHaveLength(0)
    })
  })
})
