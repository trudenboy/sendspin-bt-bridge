import { describe, it, expect, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useNotificationStore } from '@/stores/notifications'

describe('useNotificationStore', () => {
  it('adds and removes toasts', () => {
    setActivePinia(createPinia())
    const store = useNotificationStore()

    expect(store.toasts).toHaveLength(0)
    store.success('Test message')
    expect(store.toasts).toHaveLength(1)
    expect(store.toasts[0].type).toBe('success')
    expect(store.toasts[0].message).toBe('Test message')

    store.remove(store.toasts[0].id)
    expect(store.toasts).toHaveLength(0)
  })

  it('supports all toast types', () => {
    setActivePinia(createPinia())
    const store = useNotificationStore()

    store.success('s')
    store.error('e')
    store.warning('w')
    store.info('i')

    expect(store.toasts.map((t) => t.type)).toEqual([
      'success',
      'error',
      'warning',
      'info',
    ])
  })

  it('keeps errors until they are dismissed (duration 0), other types time out', () => {
    setActivePinia(createPinia())
    const store = useNotificationStore()
    store.error('e')
    store.success('s')
    expect(store.toasts.find((t) => t.type === 'error')!.duration).toBe(0)
    expect(store.toasts.find((t) => t.type === 'success')!.duration).toBeGreaterThan(0)
  })

  it('does not remove toasts on its own timer (the toast owns the countdown so hover can pause it)', () => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    const store = useNotificationStore()
    store.success('s')
    vi.advanceTimersByTime(60_000)
    expect(store.toasts).toHaveLength(1)
    vi.useRealTimers()
  })

  it('undo() adds a success toast carrying an action', () => {
    setActivePinia(createPinia())
    const store = useNotificationStore()
    const run = vi.fn()
    store.undo('Handed over', 'Undo', run)
    const t = store.toasts[0]!
    expect(t.type).toBe('success')
    expect(t.action?.label).toBe('Undo')
    expect(t.duration).toBeGreaterThan(4000)
    t.action!.run()
    expect(run).toHaveBeenCalled()
  })
})
