import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import SbToast from '@/kit/SbToast.vue'

function mountToast(props: Record<string, unknown> = {}) {
  return mount(SbToast, {
    props: { id: 1, message: 'Test notification', ...props },
  })
}

describe('SbToast', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('rendering', () => {
    it('renders the message', () => {
      const w = mountToast({ message: 'Hello world' })
      expect(w.text()).toContain('Hello world')
    })

    it('uses role="alert" for errors only and role="status" otherwise', () => {
      expect(mountToast({ type: 'error' }).find('[data-toast-id]').attributes('role')).toBe('alert')
      for (const type of ['success', 'info', 'warning'])
        expect(mountToast({ type }).find('[data-toast-id]').attributes('role')).toBe('status')
    })

    it('renders close button by default', () => {
      const w = mountToast()
      expect(w.find('[data-testid="toast-close-btn"]').exists()).toBe(true)
    })

    it('hides close button when closable is false', () => {
      const w = mountToast({ closable: false })
      expect(w.find('[data-testid="toast-close-btn"]').exists()).toBe(false)
    })
  })

  describe('type variants', () => {
    it.each([
      ['success', 'border-success'],
      ['error', 'border-error'],
      ['warning', 'border-warning'],
      ['info', 'border-info'],
    ] as const)('applies %s styling', (type, expectedClass) => {
      const w = mountToast({ type })
      expect(w.find('[data-toast-id]').classes().join(' ')).toContain(expectedClass)
    })

    it('defaults to info type', () => {
      const w = mountToast()
      expect(w.find('[data-toast-id]').classes().join(' ')).toContain('border-info')
    })
  })

  describe('icons', () => {
    it.each([
      ['success', '✓'],
      ['error', '✕'],
      ['warning', '⚠'],
      ['info', 'ℹ'],
    ] as const)('shows correct icon for %s', (type, icon) => {
      const w = mountToast({ type })
      expect(w.text()).toContain(icon)
    })
  })

  describe('dismiss behavior', () => {
    it('emits close with id when close button clicked', async () => {
      const w = mountToast({ id: 42 })
      await w.find('[data-testid="toast-close-btn"]').trigger('click')
      expect(w.emitted('close')?.[0]).toEqual([42])
    })

    it('auto-dismisses after duration', () => {
      const w = mountToast({ duration: 3000 })
      expect(w.emitted('close')).toBeFalsy()
      vi.advanceTimersByTime(3000)
      expect(w.emitted('close')?.[0]).toEqual([1])
    })

    it('does not auto-dismiss when duration is 0', () => {
      const w = mountToast({ duration: 0 })
      vi.advanceTimersByTime(10000)
      expect(w.emitted('close')).toBeFalsy()
    })

    it('clears timer on unmount', () => {
      const w = mountToast({ duration: 5000 })
      w.unmount()
      vi.advanceTimersByTime(5000)
      // No error thrown, timer was cleared
    })
  })

  describe('data attributes', () => {
    it('sets data-toast-id', () => {
      const w = mountToast({ id: 7 })
      expect(w.find('[data-toast-id]').attributes('data-toast-id')).toBe('7')
    })
  })
})

describe('SbToast close button', () => {
  it('has a 40 px hit area', () => {
    const w = mountToast()
    expect(w.find('[data-testid="toast-close-btn"]').classes()).toContain('size-10')
  })
})

describe('SbToast hover and focus pause', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('does not dismiss while hovered, and restarts the countdown on leave', async () => {
    const w = mountToast({ duration: 3000 })
    const el = w.find('[data-toast-id]')
    await el.trigger('mouseenter')
    vi.advanceTimersByTime(10_000)
    expect(w.emitted('close')).toBeFalsy()
    await el.trigger('mouseleave')
    vi.advanceTimersByTime(3000)
    expect(w.emitted('close')?.[0]).toEqual([1])
  })

  it('does not dismiss while a control inside has focus', async () => {
    const w = mountToast({ duration: 3000 })
    const el = w.find('[data-toast-id]')
    await el.trigger('focusin')
    vi.advanceTimersByTime(10_000)
    expect(w.emitted('close')).toBeFalsy()
    await el.trigger('focusout')
    vi.advanceTimersByTime(3000)
    expect(w.emitted('close')).toBeTruthy()
  })
})

describe('SbToast action', () => {
  it('renders an action button that emits action with the id and dismisses', async () => {
    const w = mountToast({ id: 5, actionLabel: 'Undo' })
    const btn = w.get('[data-testid="toast-action-btn"]')
    expect(btn.text()).toBe('Undo')
    await btn.trigger('click')
    expect(w.emitted('action')?.[0]).toEqual([5])
    expect(w.emitted('close')?.[0]).toEqual([5])
  })
  it('has no action button without a label', () => {
    expect(mountToast().find('[data-testid="toast-action-btn"]').exists()).toBe(false)
  })
})
